// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag.app: installs Tag on first run, then lists, starts and adds Tags.
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { bridge, type AppInfo, type Bridge } from "./lib/bridge";
import { compatibility, parseJSON, status, type VersionInfo } from "./lib/protocol";
import { checkUpdate, initialUpdate, installUpdate, updateReducer } from "./lib/updates";
import { useTags } from "./lib/tags";
import { useWatch } from "./lib/watch";
import { Connect } from "./components/Connect";
import { Home } from "./components/Home";
import { Installing, Welcome } from "./components/Install";
import { AI_CAPABILITY } from "./lib/ai";
import { AISettings } from "./components/AISettings";
import { Settings } from "./components/Settings";
import { TagDetail } from "./components/TagDetail";
import { Spinner, Toast } from "./components/ui";

type Screen =
  | { name: "loading" }
  | { name: "welcome" }
  | { name: "installing"; attempt: number }
  | { name: "home" }
  | { name: "connect"; args: string[] }
  | { name: "settings" }
  | { name: "ai"; tag?: string }
  | { name: "tag"; id: string };

/** Capabilities this app needs from the installed Tag. */
const NEEDED = ["list", "setup-jsonl"];
/** How often Tag checks for a complete product update. */
const APP_UPDATE_HOURS = 6;
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
  const root = useRef<HTMLElement>(null);

  useEffect(() => {
    void bridge().then(async (b) => {
      const i = await b.info();
      setApi(b);
      setInfo(i);
      setScreen(i.cli ? { name: "home" } : { name: "welcome" });
    });
  }, []);

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
    dispatchUpdate({ type: "checking" });
    try { dispatchUpdate({ type: "checked", update: await checkUpdate(api, info.version) }); }
    catch (error) { dispatchUpdate({ type: "checkFailed", error: error instanceof Error ? error.message : String(error) }); }
  }, [api, info]);

  useEffect(() => {
    if (!api || !info?.cli || screen.name !== "home") return;
    void check();
    const timer = setInterval(() => void check(), APP_UPDATE_HOURS * 3600 * 1000);
    return () => clearInterval(timer);
  }, [api, info?.cli, screen.name, check]);

  const runUpdate = useCallback(async () => {
    if (!api || !info) return;
    dispatchUpdate({ type: "updating" });
    try {
      const done = await installUpdate(api, info.version, undefined, (phase) => dispatchUpdate({ type: "phase", phase }));
      dispatchUpdate({ type: "updated", update: done });
      setOutdated(false);
      setTimeout(() => dispatchUpdate({ type: "settled" }), 3200);
    } catch (error) {
      dispatchUpdate({ type: "failed", error: error instanceof Error ? error.message : String(error) });
    } finally {
      void tags.refresh();
    }
  }, [api, info, tags]);

  // The window always fits its content, and Tag detail is wider.
  const width = screen.name === "tag" ? WIDE : WIDTH;
  useLayoutEffect(() => {
    if (!api || !root.current) return;
    const observer = new ResizeObserver(([entry]) => void api.fitWindow(width, Math.ceil(entry.target.getBoundingClientRect().height)));
    observer.observe(root.current);
    return () => observer.disconnect();
  }, [api, width, screen.name]);

  // Tray menu actions arrive here, even while the window is hidden.
  useEffect(() => {
    if (!api) return;
    return api.onTray((action, id) => {
      const row = tags.rows.find((r) => r.id === id);
      if (action === "toggle" && row) {
        if (status(row) === "setup") { setScreen({ name: "connect", args: [row.id, "setup"] }); void api.showWindow(); }
        else void tags.toggle(row);
      }
      if (action === "start-all" || action === "stop-all") void tags.all(action === "start-all" ? "start" : "stop");
      if (action === "keep-running") void tags.setAutostart(!tags.keepRunning);
      if (action === "add") setScreen({ name: "connect", args: tags.rows.length ? ["add"] : ["setup"] });
      if (action === "settings") setScreen({ name: "settings" });
    });
  }, [api, tags]);

  if (!api || !info || screen.name === "loading") {
    return <main ref={root} className="app" style={{ alignItems: "center", padding: 40 }}><Spinner /></main>;
  }
  const home = () => { setScreen({ name: "home" }); void tags.refresh(); watch.recheck(); };
  const add = () => setScreen({ name: "connect", args: tags.rows.length ? ["add"] : ["setup"] });
  return (
    <main ref={root} className={`app${info.platform === "macos" ? " overlay" : ""}${screen.name === "home" ? " home" : ""}${screen.name === "tag" ? " wide" : ""}`}>
      {screen.name === "welcome" && <Welcome api={api} platform={info.platform} install={() => setScreen({ name: "installing", attempt: 0 })} />}
      {screen.name === "installing" && (
        <Installing key={screen.attempt} api={api}
          retry={() => setScreen({ name: "installing", attempt: screen.attempt + 1 })}
          cancel={() => setScreen({ name: "welcome" })}
          done={(command) => { setInfo({ ...info, cli: command || info.cli || "tag" }); setScreen({ name: "connect", args: ["setup"] }); }} />
      )}
      {screen.name === "home" && (
        <Home api={api} tags={tags} reports={watch.reports} problems={watch.problems} activity={watch.activity}
          firstName={info.firstName ?? null} update={update} outdated={outdated} runUpdate={() => void runUpdate()}
          add={add}
          finishSetup={(row) => setScreen({ name: "connect", args: [row.id, "setup"] })}
          open={(row) => setScreen({ name: "tag", id: row.id })}
          fixAI={(tag) => setScreen({ name: "ai", tag })}
          showSettings={() => setScreen({ name: "settings" })} />
      )}
      {screen.name === "connect" && (
        <Connect api={api} args={screen.args} done={home}
          paused={() => { home(); say("Progress saved. Finish setup from Home any time."); }} />
      )}
      {screen.name === "settings" && (
        <Settings api={api} info={info} tags={tags} close={home} update={update} check={() => void check()}
          runUpdate={() => void runUpdate()} switched={(done) => dispatchUpdate({ type: "updated", update: done })}
          openAI={capabilities.includes(AI_CAPABILITY) ? () => setScreen({ name: "ai" }) : undefined} />
      )}
      {screen.name === "ai" && (
        <AISettings api={api} tags={tags} initial={screen.tag} add={add} close={() => { watch.recheck(); setScreen({ name: "settings" }); }} />
      )}
      {screen.name === "tag" && (
        <TagDetail api={api} tags={tags} initial={screen.id} problems={watch.problems} back={home} add={add}
          showSettings={() => setScreen({ name: "settings" })}
          finishSetup={(row) => setScreen({ name: "connect", args: [row.id, "setup"] })}
          openAI={(tag) => setScreen({ name: "ai", tag })} say={say} />
      )}
      <Toast text={toast} />
    </main>
  );
}
