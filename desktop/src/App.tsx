// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag.app: installs Tag on first run, then lists, starts and adds Tags.
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { bridge, type AppInfo, type Bridge } from "./lib/bridge";
import { compatibility, isDraft, parseJSON, parseList, status, type VersionInfo } from "./lib/protocol";
import { checkUpdate, initialUpdate, installUpdate, updateReducer } from "./lib/updates";
import { useTags } from "./lib/tags";
import { TrackContext, useTelemetry, type AppEvents } from "./lib/telemetry";
import { useWatch } from "./lib/watch";
import { deepLinkTarget } from "./lib/deeplink";
import { Connect } from "./components/Connect";
import { Home } from "./components/Home";
import { Installing, Starting, Welcome } from "./components/Install";
import { AI_CAPABILITY, SHARED_AI_CAPABILITY } from "./lib/ai";
import { AISettings } from "./components/AISettings";
import { Settings, type SettingsTab } from "./components/Settings";
import { TagDetail } from "./components/TagDetail";
import { TelemetryNotice } from "./components/TelemetryNotice";
import { Toast } from "./components/ui";

type Screen =
  | { name: "loading" }
  | { name: "welcome" }
  | { name: "installing"; attempt: number }
  | { name: "home" }
  | { name: "connect"; args: string[] }
  | { name: "settings"; tab?: SettingsTab }
  | { name: "ai"; resume?: string[] }
  | { name: "tag"; id: string }
  /** Settings > Replay onboarding: the first-run screens again, changing nothing. */
  | { name: "replay"; step: "telemetry" | "welcome" };

/** What each screen counts as in usage data. */
const SCREEN_EVENT: Partial<Record<Screen["name"], AppEvents["app_screen_viewed"]["screen"]>> = {
  home: "home", tag: "tag", settings: "settings", ai: "ai_settings", connect: "setup",
};

/** Capabilities this app needs from the installed Tag. */
const NEEDED = ["list", "setup-jsonl"];
/** How often Tag checks for a complete product update. */
const APP_UPDATE_HOURS = 6;
const DEVELOPMENT_UPDATES = "Updates are off in development builds. Use the installed Tag.app to update.";
/** Window widths: everyday screens, and Tag detail's Slack layout. */
export const WIDTH = 520;
export const WIDE = 800;

/** Keep the Tags the Swift app restored at login, then let the login service do it. */
export async function migrateFromSwiftApp(api: Bridge, legacy: string[], existing: string[]) {
  const version = await api.tag(["version", "--json"]);
  if (version.code !== 0 || !parseJSON<VersionInfo>(version.stdout).capabilities.includes("autostart-keep")) return false;
  const keep = legacy.filter((id) => existing.includes(id));
  if (keep.length && (await api.tag(["autostart", "keep", ...keep, "--json"])).code !== 0) return false;
  if ((await api.tag(["autostart", "on", "--json"])).code !== 0) return false;
  // Done only once the result is verified; otherwise the next launch retries.
  const status = await api.tag(["autostart", "status", "--json"]);
  if (status.code !== 0) return false;
  const result = parseJSON<{ enabled: boolean; tags: { tag: string; keep_running: boolean }[] }>(status.stdout);
  const kept = new Set(result.tags.filter((t) => t.keep_running).map((t) => t.tag));
  if (!result.enabled || !keep.every((id) => kept.has(id))) return false;
  await api.markMigrated();
  return true;
}

export function App() {
  const [api, setApi] = useState<Bridge | null>(null);
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [screen, setScreen] = useState<Screen>({ name: "loading" });
  const [bootError, setBootError] = useState("");
  const [bootAttempt, setBootAttempt] = useState(0);
  const [outdated, setOutdated] = useState(false);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [update, dispatchUpdate] = useReducer(updateReducer, initialUpdate);
  const [toast, setToast] = useState<string | null>(null);
  const installed = !!info?.cli && !["loading", "welcome", "installing"].includes(screen.name);
  const tags = useTags(api, installed);
  const watch = useWatch(api, tags.rows, {
    ai: installed && capabilities.includes(AI_CAPABILITY),
    activity: installed && capabilities.includes("logs-activity"),
  });
  const telemetry = useTelemetry(api, !!info?.cli);
  const { track } = telemetry;
  const [root, setRoot] = useState<HTMLElement | null>(null);

  useEffect(() => {
    let live = true;
    setBootError("");
    void bridge().then(async (b) => {
      const i = await b.info();
      if (!live) return;
      setApi(b);
      setInfo(i);
      setScreen(i.cli ? { name: "home" } : { name: "welcome" });
    }).catch((error) => { if (live) setBootError(String(error)); });
    return () => { live = false; };
  }, [bootAttempt]);

  const say = useCallback((text: string) => {
    setToast(text);
    setTimeout(() => setToast((current) => (current === text ? null : current)), 2600);
  }, []);

  // Make sure this Tag can be driven by this app.
  useEffect(() => {
    if (!api || !info?.cli || screen.name !== "home") return;
    // A failed call or unreadable output counts as outdated, so the notice offers an update.
    void api.tag(["version", "--json"]).then((r) => {
      try {
        const version = parseJSON<VersionInfo>(r.stdout);
        setOutdated(!(r.code === 0 && compatibility(version, NEEDED).ok));
        setCapabilities(r.code === 0 ? version.capabilities : []);
      } catch {
        setOutdated(true);
      }
    }, () => setOutdated(true));
  }, [api, info?.cli, screen.name]);

  // Usage data: once per run, then each screen. Nothing is recorded until the person has chosen.
  const opened = useRef(false);
  useEffect(() => {
    if (!info || !telemetry.recording || opened.current) return;
    opened.current = true;
    track("app_opened", { app_version: info.version });
  }, [info, track, telemetry.recording]);
  useEffect(() => {
    const viewed = SCREEN_EVENT[screen.name];
    if (viewed) track("app_screen_viewed", { screen: viewed });
  }, [screen.name, track]);

  // Once: the Swift Tag.app restored Tags at login itself; the CLI's login service does that now.
  const migrating = useRef(false);
  useEffect(() => {
    const legacy = info?.legacyWantedTags;
    if (!api || !legacy?.length || !tags.loaded || migrating.current) return;
    migrating.current = true;
    void migrateFromSwiftApp(api, legacy, tags.rows.map((r) => r.id)).catch(() => false).then((done) => {
      if (done) setInfo((current) => current && { ...current, legacyWantedTags: null });
      void tags.refresh();
      void tags.refreshAutostart();
    });
  }, [api, info?.legacyWantedTags, tags.loaded, tags.rows, tags]);

  // Check the complete product; installation always waits for a click.
  const check = useCallback(async () => {
    if (!api || !info) return;
    // A development build's version names its release line, not a release; updating would replace it.
    if (info.development) { dispatchUpdate({ type: "checkFailed", error: DEVELOPMENT_UPDATES }); return; }
    dispatchUpdate({ type: "checking" });
    try { dispatchUpdate({ type: "checked", update: await checkUpdate(api, info.version) }); }
    catch (error) { dispatchUpdate({ type: "checkFailed", error: error instanceof Error ? error.message : String(error) }); }
  }, [api, info]);

  useEffect(() => {
    if (!api || !info?.cli || info.development || screen.name !== "home") return;
    void check();
    const timer = setInterval(() => void check(), APP_UPDATE_HOURS * 3600 * 1000);
    return () => clearInterval(timer);
  }, [api, info?.cli, info?.development, screen.name, check]);

  const runUpdate = useCallback(async () => {
    if (!api || !info || info.development) return;
    dispatchUpdate({ type: "updating" });
    try {
      const done = await installUpdate(api, info.version, undefined, (phase) => dispatchUpdate({ type: "phase", phase }));
      dispatchUpdate({ type: "updated", update: done });
      setOutdated(false);
      track("app_update_finished", { outcome: "succeeded" });
      setTimeout(() => dispatchUpdate({ type: "settled" }), 3200);
    } catch (error) {
      dispatchUpdate({ type: "failed", error: error instanceof Error ? error.message : String(error) });
      track("app_update_finished", { outcome: "failed" });
    } finally {
      void tags.refresh();
      telemetry.reload();
    }
  }, [api, info, tags, track, telemetry.reload]);

  // The window always fits its content, and Tag detail is wider.
  const width = screen.name === "tag" ? WIDE : WIDTH;
  useLayoutEffect(() => {
    if (!api || !root) return;
    const observer = new ResizeObserver(([entry]) => void api.fitWindow(width, Math.ceil(entry.target.getBoundingClientRect().height)));
    observer.observe(root);
    return () => observer.disconnect();
  }, [api, width, root]);

  // Tray menu actions arrive here, even while the window is hidden.
  useEffect(() => {
    if (!api) return;
    return api.onTray((action, id) => {
      const row = tags.rows.find((r) => r.id === id);
      if (action === "toggle" && row) {
        if (status(row) === "setup") { setScreen({ name: "connect", args: [row.id, "setup"] }); void api.showWindow(); }
        else void tags.toggle(row);
      }
      // Removing is confirmed on the Tag's own screen, never straight from the menu.
      if (action === "remove" && row) { setScreen({ name: "tag", id: row.id }); void api.showWindow(); }
      if (action === "start-all" || action === "stop-all") void tags.all(action === "start-all" ? "start" : "stop");
      if (action === "keep-running") void tags.setAutostart(!tags.keepRunning);
      if (action === "add") setScreen({ name: "connect", args: tags.rows.length ? ["add"] : ["setup"] });
      if (action === "settings") setScreen({ name: "settings" });
    });
  }, [api, tags]);

  // Links from Slack, such as "Choose another model in the Tag app", open that Tag's Details.
  const ready = installed && tags.loaded;
  const [link, setLink] = useState<string | null>(null);
  useEffect(() => api?.onDeepLink(setLink), [api]);
  useEffect(() => {
    if (!api || !link || !ready) return;
    setLink(null);
    const id = deepLinkTarget(link);
    if (id && tags.rows.some((r) => r.id === id)) setScreen({ name: "tag", id });
    else if (id) say("That Tag isn't on this computer.");
    void api.showWindow();
  }, [api, link, ready, tags.rows, say]);

  // Tag was removed after this app started, even while Home is showing: offer to install it again.
  useEffect(() => {
    if (!api || screen.name !== "home" || !tags.error) return;
    let live = true;
    void api.info().then((i) => {
      if (!live || i.cli) return;
      setInfo(i);
      setScreen({ name: "welcome" });
    }, () => {});
    return () => { live = false; };
  }, [api, screen.name, tags.error]);

  // Setups that never reached Slack are set aside once the setup flow has ended.
  useEffect(() => {
    if (screen.name !== "home" && screen.name !== "tag") return;
    const timer = setTimeout(() => void tags.discardDrafts(), 2000);
    return () => clearTimeout(timer);
  }, [screen.name, tags.rows, tags.discardDrafts]);
  if (!api || !info || screen.name === "loading") {
    return <main ref={setRoot} className="app"><Starting error={bootError} retry={() => setBootAttempt((value) => value + 1)} /></main>;
  }
  // The usage data notice comes before Home and setup, so it's seen before anything is recorded.
  if (screen.name === "home" || screen.name === "connect") {
    if (!telemetry.loaded) return <main ref={setRoot} className="app"><Starting retry={telemetry.reload} /></main>;
    if (telemetry.asking) return <main ref={setRoot} className="app"><TelemetryNotice api={api} telemetry={telemetry} /></main>;
  }
  const home = () => { setScreen({ name: "home" }); void tags.refresh(); watch.recheck(); };
  // A reinstall keeps existing Tags: return to them instead of setting up a first Tag again.
  const afterInstall = async (command: string) => {
    setInfo({ ...info, cli: command || info.cli || "tag" });
    const listed = await api.tag(["list", "--json"]).catch(() => null);
    let empty = false;
    // Only a list that reads cleanly may start first-Tag setup; otherwise Home shows the error and retries.
    try { empty = !!listed && listed.code === 0 && !parseList(listed.stdout).some((row) => !isDraft(row)); } catch { /* go Home */ }
    if (empty) setScreen({ name: "connect", args: ["setup"] }); else home();
  };
  const add = () => setScreen({ name: "connect", args: tags.rows.length ? ["add"] : ["setup"] });
  return (
    <TrackContext.Provider value={track}>
      <main ref={setRoot} className={`app${info.platform === "macos" ? " overlay" : ""}${screen.name === "home" ? " home" : ""}${screen.name === "tag" ? " wide" : ""}`}>
        {screen.name === "replay" && screen.step === "telemetry" && <TelemetryNotice api={api} telemetry={telemetry} preview={() => setScreen({ name: "replay", step: "welcome" })} />}
        {screen.name === "replay" && screen.step === "welcome" && <Welcome api={api} platform={info.platform} preview={() => setScreen({ name: "settings", tab: "about" })} />}
        {screen.name === "welcome" && <Welcome api={api} platform={info.platform} install={() => setScreen({ name: "installing", attempt: 0 })} />}
        {screen.name === "installing" && (
          <Installing key={screen.attempt} api={api}
            retry={() => setScreen({ name: "installing", attempt: screen.attempt + 1 })}
            cancel={() => setScreen({ name: "welcome" })}
            done={(command) => void afterInstall(command)} />
        )}
        {screen.name === "home" && !tags.loaded && <Starting error={tags.error} retry={() => void tags.refresh()} />}
        {screen.name === "home" && tags.loaded && (
          <Home api={api} tags={tags} reports={watch.reports} problems={watch.problems} activity={watch.activity}
            firstName={info.firstName ?? null} update={update} outdated={outdated} runUpdate={() => void runUpdate()}
            add={add}
            finishSetup={(row) => setScreen({ name: "connect", args: [row.id, "setup"] })}
            open={(row) => setScreen({ name: "tag", id: row.id })}
            fixAI={() => capabilities.includes(SHARED_AI_CAPABILITY) ? setScreen({ name: "ai" }) : say("Update Tag to manage shared AI accounts in Settings.")}
            showSettings={(tab) => setScreen({ name: "settings", tab })} />
        )}
        {screen.name === "connect" && (
          <Connect api={api} args={screen.args} start={(id, onLine) => tags.start(id, capabilities.includes("start-progress") ? onLine : undefined)} openAI={capabilities.includes(SHARED_AI_CAPABILITY) ? (resume) => setScreen({ name: "ai", resume }) : undefined} done={home}
            paused={() => { home(); say("Progress saved. Finish setup from Home any time."); }} />
        )}
        {screen.name === "settings" && (
          <Settings key={screen.tab} initialTab={screen.tab} api={api} info={info} tags={tags} telemetry={telemetry} close={home} update={update} check={() => void check()}
            runUpdate={() => void runUpdate()} replay={() => setScreen({ name: "replay", step: "telemetry" })} switched={(done) => dispatchUpdate({ type: "updated", update: done })}
            openAI={capabilities.includes(SHARED_AI_CAPABILITY) ? () => setScreen({ name: "ai" }) : undefined} />
        )}
        {screen.name === "ai" && (
          <AISettings api={api} tags={tags} close={() => { watch.recheck(); setScreen(screen.resume ? { name: "connect", args: screen.resume } : { name: "settings" }); }} />
        )}
        {screen.name === "tag" && (
          <TagDetail api={api} tags={tags} initial={screen.id} problems={watch.problems} back={home} add={add}
            canDescribe={capabilities.includes("describe")}
            showSettings={() => setScreen({ name: "settings" })}
            finishSetup={(row) => setScreen({ name: "connect", args: [row.id, "setup"] })}
            openAI={() => capabilities.includes(SHARED_AI_CAPABILITY) ? setScreen({ name: "ai" }) : say("Update Tag to manage shared AI accounts in Settings.")} say={say} />
        )}
        <Toast text={toast} />
      </main>
    </TrackContext.Provider>
  );
}
