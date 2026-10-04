// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Tag.app: installs Tag on first run, then lists, starts and adds Tags.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { bridge, type AppInfo, type Bridge } from "./lib/bridge";
import { compatibility, parseJSON, status, type TagRow, type VersionInfo } from "./lib/protocol";
import { checkUpdate, type ProductUpdate } from "./lib/updates";
import { useTags } from "./lib/tags";
import { Connect } from "./components/Connect";
import { Home } from "./components/Home";
import { Installing, Welcome } from "./components/Install";
import { Logs, Settings } from "./components/Settings";
import { ErrorLine, Header, Primary, Spinner } from "./components/ui";

type Screen =
  | { name: "loading" }
  | { name: "welcome" }
  | { name: "installing"; attempt: number }
  | { name: "home" }
  | { name: "connect"; args: string[] }
  | { name: "settings" }
  | { name: "logs"; row: TagRow };

/** Capabilities this app needs from the installed Tag. */
const NEEDED = ["list", "setup-jsonl"];
/** How often Tag checks for a complete product update. */
const APP_UPDATE_HOURS = 6;

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
  const [appUpdate, setAppUpdate] = useState<ProductUpdate | null>(null);
  const installed = !!info?.cli && !["loading", "welcome", "installing"].includes(screen.name);
  const tags = useTags(api, installed);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void bridge().then(async (b) => {
      const i = await b.info();
      setApi(b);
      setInfo(i);
      setScreen(i.cli ? { name: "home" } : { name: "welcome" });
    });
  }, []);

  // Make sure this Tag can be driven by this app.
  useEffect(() => {
    if (!api || !info?.cli || screen.name !== "home") return;
    // A failed call or unreadable output counts as outdated, so the banner offers an update.
    void api.tag(["version", "--json"]).then((r) => {
      try {
        setOutdated(!(r.code === 0 && compatibility(parseJSON<VersionInfo>(r.stdout), NEEDED).ok));
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
  useEffect(() => {
    if (!api || !info?.cli || screen.name !== "home") return;
    const check = () => void checkUpdate(api, info.version).then((update) => {
      setAppUpdate(update.runtime || update.desktop ? update : null);
    }).catch(() => {});
    check();
    const timer = setInterval(check, APP_UPDATE_HOURS * 3600 * 1000);
    return () => clearInterval(timer);
  }, [api, info, screen.name]);

  // The window always fits its content.
  useLayoutEffect(() => {
    if (!api || !root.current) return;
    const observer = new ResizeObserver(([entry]) => void api.fitWindow(Math.ceil(entry.target.getBoundingClientRect().height)));
    observer.observe(root.current);
    return () => observer.disconnect();
  }, [api]);

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
    return <div ref={root} className="app" style={{ alignItems: "center" }}><Spinner /></div>;
  }
  const home = () => { setScreen({ name: "home" }); void tags.refresh(); };
  return (
    <main ref={root} className="app">
      {screen.name === "welcome" && <Welcome api={api} platform={info.platform} install={() => setScreen({ name: "installing", attempt: 0 })} />}
      {screen.name === "installing" && (
        <Installing key={screen.attempt} api={api}
          retry={() => setScreen({ name: "installing", attempt: screen.attempt + 1 })}
          done={(command) => { setInfo({ ...info, cli: command || info.cli || "tag" }); setScreen({ name: "home" }); }} />
      )}
      {screen.name === "home" && (
        <>
          {appUpdate && (
            <div className="well row gap-10">
              <span className="stack gap-4" style={{ flex: 1 }}>
                <span style={{ fontWeight: 600 }}>Tag {appUpdate.version} is ready</span>
                <span className="caption secondary">Update the app and your Tags together.</span>
              </span>
              <Primary title="Update Tag" onClick={() => setScreen({ name: "settings" })} />
            </div>
          )}
          {outdated && (
            <div className="well row gap-10">
              <ErrorLine>Tag needs an update to continue.</ErrorLine>
              <div className="spacer" />
              <Primary title="Update" onClick={() => setScreen({ name: "settings" })} />
            </div>
          )}
          <Home api={api} tags={tags}
            add={() => setScreen({ name: "connect", args: tags.rows.length ? ["add"] : ["setup"] })}
            finishSetup={(row) => setScreen({ name: "connect", args: [row.id, "setup"] })}
            showLogs={(row) => setScreen({ name: "logs", row })}
            showSettings={() => setScreen({ name: "settings" })} />
        </>
      )}
      {screen.name === "connect" && (
        <>
          <Header title="Add a Tag" subtitle="Connect a Slack workspace" />
          <Connect api={api} args={screen.args} done={home} />
        </>
      )}
      {screen.name === "settings" && (
        <Settings api={api} info={info} tags={tags} close={home} />
      )}
      {screen.name === "logs" && <Logs api={api} row={screen.row} close={home} />}
    </main>
  );
}
