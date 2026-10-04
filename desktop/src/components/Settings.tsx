// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// App settings, updates, and a Tag's recent logs.
import { useCallback, useEffect, useState } from "react";
import type { AppInfo, Bridge } from "../lib/bridge";
import { parseJSON, title, type TagRow } from "../lib/protocol";
import { failureLine, type Tags } from "../lib/tags";
import { checkUpdate, installUpdate, type ProductUpdate } from "../lib/updates";
import { CommunityLinks } from "./CommunityLinks";
import { Back, ErrorLine, Heading, Icon, Primary, Secondary, Spinner, Switch } from "./ui";

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
}

export function Settings({ api, info, tags, close }: SettingsProps) {
  const [login, setLogin] = useState(false);
  const [keepBusy, setKeepBusy] = useState(false);
  const [tagVersion, setTagVersion] = useState("");
  const [update, setUpdate] = useState<ProductUpdate | null>(null);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void api.openAtLogin().then(setLogin).catch(() => setLogin(false));
    void api.tag(["version", "--json"]).then((r) => {
      if (r.code === 0) setTagVersion(parseJSON<{ version: string }>(r.stdout).version);
    }).catch(() => {});
  }, [api]);

  const check = useCallback(async () => {
    setChecking(true);
    setError("");
    setUpdate(null);
    try { setUpdate(await checkUpdate(api, info.version)); }
    catch (error) { setError(String(error)); }
    finally { setChecking(false); }
  }, [api, info.version]);

  useEffect(() => { void check(); }, [check]);

  const upgrade = async () => {
    setUpgrading(true);
    setError("");
    try {
      const done = await installUpdate(api, info.version);
      setUpdate(done);
      setTagVersion(done.version);
    } catch (error) {
      setUpdate(null);
      setError(String(error));
    } finally {
      setUpgrading(false);
      void tags.refresh();
    }
  };
  const available = update && (update.runtime || update.desktop);

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
              {available ? `Tag ${update.version} is available` : `Tag ${tagVersion || info.version}`}
            </span>
            <span className="caption secondary">
              {available ? "Updates Tag and restarts the app if needed. Your settings are kept."
                : update ? "Tag is up to date" : "One update for the app and your Tags"}
            </span>
          </span>
          {upgrading ? <><Spinner small /><span className="caption secondary">Updating…</span></>
            : available ? <Primary title="Update Tag" onClick={() => void upgrade()} />
            : <Secondary title={checking ? "Checking…" : "Check for updates"} disabled={checking} onClick={() => void check()} />}
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
