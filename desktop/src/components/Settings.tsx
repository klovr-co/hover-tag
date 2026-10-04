// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// App settings, updates, and a Tag's recent logs.
import { useCallback, useEffect, useState } from "react";
import type { AppInfo, Bridge } from "../lib/bridge";
import { parseJSON, title, type TagRow } from "../lib/protocol";
import { failureLine, type Tags } from "../lib/tags";
import { APP_CHANNELS, checkUpdate, installUpdate, isAppChannel, type Channel, type ProductUpdate } from "../lib/updates";
import { AISummaryRow } from "./AISettings";
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

const CHANNEL_LABEL: Record<Channel, string> = { stable: "Stable", beta: "Beta", alpha: "Alpha" };
const CHANNEL_DETAIL: Record<Channel, string> = {
  stable: "Tested releases. Recommended for most people.",
  beta: "New features a little earlier, once they're mostly finished.",
  alpha: "The newest changes as soon as they're released. Things may break.",
};

/** Choose the release channel the app and your Tags follow, after confirming what changes. */
function ReleaseChannel({ api, appVersion, update, busy, setBusy, switched, setError }: {
  api: Bridge; appVersion: string; update: ProductUpdate | null; busy: boolean;
  setBusy: (on: boolean) => void; switched: (done: ProductUpdate, restarting: boolean) => void; setError: (message: string) => void;
}) {
  const [choice, setChoice] = useState<Channel | null>(null);
  const [preview, setPreview] = useState<ProductUpdate | null>(null);
  const following = update?.pinned ? null : update?.channel ?? null;

  const pick = async (channel: Channel) => {
    if (channel === following) return;
    setChoice(channel);
    setPreview(null);
    setError("");
    setBusy(true);
    try { setPreview(await checkUpdate(api, appVersion, channel)); }
    catch (error) { setChoice(null); setError(String(error)); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!choice) return;
    setBusy(true);
    setError("");
    try {
      // Installing a new Tag.app restarts it; otherwise refresh what Settings shows.
      switched(await installUpdate(api, appVersion, choice), !!preview?.desktop);
      setChoice(null);
      setPreview(null);
    } catch (error) { setError(String(error)); }
    finally { setBusy(false); }
  };
  const name = choice ? CHANNEL_LABEL[choice] : "";
  const installs = preview && (preview.runtime || preview.desktop);
  return (
    <div className="list-item stack gap-10" style={{ alignItems: "stretch" }}>
      <div className="row gap-12">
        <span className="stack gap-4" style={{ flex: 1 }}>
          <span style={{ fontWeight: 500 }}>Release channel</span>
          <span className="caption secondary">
            {update?.pinned ? `Pinned to Tag ${update.current}. Choose a channel to follow one again.`
              : following === "edge" ? "Following edge, chosen in the terminal. Tag.app doesn't follow edge; choose a channel to update the app too."
              : isAppChannel(choice ?? following) ? CHANNEL_DETAIL[(choice ?? following) as Channel]
              : "The app and your Tags follow the same channel."}
          </span>
        </span>
        <div className="segmented" role="radiogroup" aria-label="Release channel">
          {APP_CHANNELS.map((channel) => (
            <button key={channel} role="radio" aria-checked={(choice ?? following) === channel}
              disabled={busy || !update} onClick={() => void pick(channel)}>{CHANNEL_LABEL[channel]}</button>
          ))}
        </div>
      </div>
      {choice && !preview && <span className="caption secondary row gap-6"><Spinner small />Checking {name}…</span>}
      {choice && preview && (
        <div className="well row gap-10">
          <span className="caption" style={{ flex: 1 }}>
            {installs ? `Switch to ${name} and update to Tag ${preview.version}. The app and your Tags update together, and your settings are kept.`
              : preview.ahead ? `${name}'s newest release is older than Tag ${preview.current}. Tag keeps ${preview.current} and follows ${name} from its next release.`
              : `Switch to ${name}. You already have its newest release.`}
          </span>
          <Secondary title="Cancel" disabled={busy} onClick={() => { setChoice(null); setPreview(null); }} />
          <Primary title={busy ? "Switching…" : installs ? "Switch and update" : "Switch"} disabled={busy} onClick={() => void confirm()} />
        </div>
      )}
    </div>
  );
}

interface SettingsProps {
  api: Bridge;
  info: AppInfo;
  tags: Tags;
  close: () => void;
  /** Opens Settings → AI & models. */
  openAI?: () => void;
}

export function Settings({ api, info, tags, close, openAI }: SettingsProps) {
  const [login, setLogin] = useState(false);
  const [keepBusy, setKeepBusy] = useState(false);
  const [tagVersion, setTagVersion] = useState("");
  const [update, setUpdate] = useState<ProductUpdate | null>(null);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [switching, setSwitching] = useState(false);
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
      {openAI && <AISummaryRow api={api} tags={tags} open={openAI} />}
      <div className="stack gap-8">
        <div className="headline" style={{ padding: "0 4px" }}>Updates</div>
        <div className="card">
        <div className="list-item" style={{ gap: 12 }}>
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
            : <Secondary title={checking ? "Checking…" : "Check for updates"} disabled={checking || switching} onClick={() => void check()} />}
        </div>
        <ReleaseChannel api={api} appVersion={info.version} update={update} busy={switching || upgrading || checking}
          setBusy={setSwitching} setError={setError}
          switched={(done, restarting) => { setTagVersion(done.version); setUpdate(done); void tags.refresh(); if (!restarting) void check(); }} />
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
