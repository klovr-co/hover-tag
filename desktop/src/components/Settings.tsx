// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// App settings, updates, and a Tag's recent logs.
import { useCallback, useEffect, useState } from "react";
import type { AppInfo, AppUpdate, Bridge } from "../lib/bridge";
import { parseJSON, title, type TagRow } from "../lib/protocol";
import { failureLine, type Tags } from "../lib/tags";
import { CommunityLinks } from "./CommunityLinks";
import { Back, ErrorLine, Heading, Icon, Primary, Secondary, Spinner, Switch } from "./ui";

interface Upgrade {
  status: string;
  current?: { version?: string };
  target?: { version?: string };
  restart_required?: boolean;
}

function Toggle({ label, detail, on, busy, onChange }: {
  label: string; detail: string; on: boolean; busy?: boolean; onChange: (on: boolean) => void;
}) {
  return (
    <div className="list-item" style={{ gap: 16 }}>
      <span className="stack gap-4" style={{ flex: 1 }}>
        <span style={{ fontWeight: 500 }}>{label}</span>
        <span className="caption secondary">{detail}</span>
      </span>
      <Switch on={on} busy={!!busy} label={label} onClick={() => onChange(!on)} />
    </div>
  );
}

interface SettingsProps {
  api: Bridge;
  info: AppInfo;
  tags: Tags;
  close: () => void;
  appUpdate: AppUpdate | null;
  checkApp: () => Promise<AppUpdate | null>;
  installApp: () => void;
  installingApp: boolean;
}

export function Settings({ api, info, tags, close, appUpdate, checkApp, installApp, installingApp }: SettingsProps) {
  const [appChecked, setAppChecked] = useState(false);
  const [checkingApp, setCheckingApp] = useState(false);
  const [login, setLogin] = useState(false);
  const [keepBusy, setKeepBusy] = useState(false);
  const [tagVersion, setTagVersion] = useState("");
  const [update, setUpdate] = useState<Upgrade | null>(null);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void api.openAtLogin().then(setLogin).catch(() => setLogin(false));
    void api.tag(["version", "--json"]).then((r) => {
      if (r.code === 0) setTagVersion(parseJSON<{ version: string }>(r.stdout).version);
    });
  }, [api]);

  const check = useCallback(async () => {
    setChecking(true);
    setError("");
    const result = await api.tag(["upgrade", "--dry-run", "--json"]);
    setChecking(false);
    if (result.code === 0) setUpdate(parseJSON<Upgrade>(result.stdout));
    else setError(failureLine(result, "Couldn't check for updates."));
  }, [api]);

  const upgrade = async () => {
    setUpgrading(true);
    setError("");
    const result = await api.tag(["upgrade", "--json"]);
    setUpgrading(false);
    if (result.code === 0) {
      const done = parseJSON<Upgrade>(result.stdout);
      setUpdate({ ...done, status: done.status === "upgraded" ? "current" : done.status });
      setTagVersion(done.target?.version ?? tagVersion);
      void tags.refresh();
    } else setError(failureLine(result, "The upgrade didn't finish. Your Tags still use the previous version."));
  };

  return (
    <div className="stack gap-20">
      <div className="row gap-10">
        <Back onClick={close} />
        <div className="spacer" />
      </div>
      <Heading title="Settings" />
      <div className="card">
        <Toggle label="Open Tag at login" detail="Keep Tag in the menu bar after you log in."
          on={login} onChange={(on) => void api.openAtLogin(on).then(setLogin).catch((e) => setError(String(e)))} />
        <Toggle label="Keep Tags running"
          detail="Start the Tags you switched on after you log in, and restart any that stop. Works even when this app is closed."
          on={tags.keepRunning} busy={keepBusy}
          onChange={async (on) => { setKeepBusy(true); await tags.setAutostart(on); setKeepBusy(false); }} />
      </div>
      <div className="stack gap-8">
        <div className="headline" style={{ padding: "0 4px" }}>Updates</div>
        <div className="card list-item" style={{ gap: 12 }}>
          <span className="stack gap-4" style={{ flex: 1 }}>
            <span style={{ fontWeight: 500 }}>
              {update?.status === "available" ? `Tag ${update.target?.version} is available`
                : update?.status === "current" ? "Tag is up to date" : `Tag ${tagVersion || "…"}`}
            </span>
            <span className="caption secondary">
              {update?.status === "available" ? "Running Tags restart on the new version. Your settings are kept."
                : "The tag command and your Tags"}
            </span>
          </span>
          {upgrading ? <><Spinner small /><span className="caption secondary">Upgrading…</span></>
            : update?.status === "available" ? <Primary title="Upgrade" onClick={() => void upgrade()} />
            : <Secondary title={checking ? "Checking…" : "Check for updates"} disabled={checking} onClick={() => void check()} />}
        </div>
        <div className="card list-item" style={{ gap: 12 }}>
          <span className="stack gap-4" style={{ flex: 1 }}>
            <span style={{ fontWeight: 500 }}>
              {appUpdate ? `Tag.app ${appUpdate.version} is available`
                : appChecked ? "Tag.app is up to date" : `Tag.app ${info.version}`}
            </span>
            <span className="caption secondary">
              {appUpdate ? "Restarts the app; your Tags keep running." : "Checks automatically every few hours"}
            </span>
          </span>
          {installingApp ? <><Spinner small /><span className="caption secondary">Updating…</span></>
            : appUpdate ? <Primary title="Restart to update" onClick={installApp} />
            : <Secondary title={checkingApp ? "Checking…" : "Check for updates"} disabled={checkingApp}
                onClick={async () => {
                  setCheckingApp(true);
                  try { await checkApp(); setAppChecked(true); }
                  catch { setError("Couldn't check for a new Tag.app. Check your connection and try again."); }
                  setCheckingApp(false);
                }} />}
        </div>
      </div>
      {(error || tags.error) && <ErrorLine>{error || tags.error}</ErrorLine>}
      <div className="divider" />
      <CommunityLinks api={api} />
    </div>
  );
}

export function Logs({ api, row, close }: { api: Bridge; row: TagRow; close: () => void }) {
  const [logs, setLogs] = useState<Record<string, string[]> | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const result = await api.tag([row.id, "logs", "--json", "--limit", "200"]);
    if (result.code === 0) { setLogs(parseJSON<{ services: Record<string, string[]> }>(result.stdout).services); setError(""); }
    else setError(failureLine(result, "Couldn't read this Tag's logs."));
  }, [api, row.id]);
  useEffect(() => { void load(); }, [load]);
  const text = Object.entries(logs ?? {}).map(([name, lines]) => `── ${name} ──\n${lines.join("\n") || "No recent entries"}`).join("\n\n");
  return (
    <div className="stack gap-14">
      <div className="row gap-8">
        <Back onClick={close} />
        <div className="spacer" />
        <button className="icon-btn" title="Refresh" aria-label="Refresh logs" onClick={() => void load()}><Icon name="refresh" /></button>
      </div>
      <Heading title={`${title(row)} logs`} body="Recent output from this Tag's services. Tokens are hidden." />
      {logs === null && !error ? <Spinner /> : <pre className="log tall selectable mono">{text || "No logs yet."}</pre>}
      {error && <ErrorLine>{error}</ErrorLine>}
      <div className="row gap-8">
        <div className="spacer" />
        <Secondary title="Copy" icon="copy" onClick={() => void api.copy(text)} />
      </div>
    </div>
  );
}
