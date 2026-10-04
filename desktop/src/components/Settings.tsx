// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// App settings, the one Tag update, and a Tag's recent logs.
import { useEffect, useState, type ReactNode } from "react";
import type { AppInfo, Bridge } from "../lib/bridge";
import { aiArgs, parseStatus } from "../lib/ai";
import { parseJSON } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import {
  APP_CHANNELS, checkUpdate, installUpdate, isAppChannel, targetVersion,
  type Channel, type ProductUpdate, type UpdateState,
} from "../lib/updates";
import building from "../assets/art/tag-building.png";
import puzzled from "../assets/art/tag-puzzled.png";
import { configuredTags } from "./AISettings";
import { CommunityLinks } from "./CommunityLinks";
import { CompactSky, ErrorLine, Icon, Primary, Secondary, Spinner, Switch, tagIcon } from "./ui";

function SetRow({ label, detail, children }: { label: ReactNode; detail: ReactNode; children?: ReactNode }) {
  return (
    <div className="r set" style={{ padding: "13px 14px", gap: 16 }}>
      <div className="txt"><span className="label" style={{ fontSize: 14.5 }}>{label}</span><span className="sub wrap">{detail}</span></div>
      {children}
    </div>
  );
}

const Busy = ({ text }: { text: string }) => (
  <span className="meta" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><Spinner small label={text} />{text}</span>
);

const UpToDate = () => (
  <span className="meta" style={{ color: "var(--green)", fontWeight: 600, display: "inline-flex", gap: 4, alignItems: "center" }}>
    <Icon name="check" size={12} />Up to date
  </span>
);

export const CHANNEL_LABEL: Record<Channel, string> = { stable: "Stable", beta: "Beta", alpha: "Alpha" };
const CHANNEL_DETAIL: Record<Channel, string> = {
  stable: "Tested releases. Recommended for most people.",
  beta: "New features a little earlier, once they're mostly finished.",
  alpha: "The newest changes as soon as they're released. Things may break.",
};

/** What switching to a channel would do, in a sentence. */
export function previewText(name: string, preview: ProductUpdate) {
  if (preview.runtime || preview.desktop) {
    return `Switch to ${name} and update to Tag ${preview.version}. The app and your Tags update together, and your settings are kept.`;
  }
  if (preview.ahead) return `${name}'s newest release is older than Tag ${preview.current}. Tag keeps ${preview.current} and follows ${name} from its next release.`;
  return `Switch to ${name}. You already have its newest release.`;
}

/** Choose the release channel the app and your Tags follow, after confirming what changes. */
export function ReleaseChannel({ api, appVersion, update, busy, setBusy, switched, setError }: {
  api: Bridge; appVersion: string; update: ProductUpdate | null; busy: boolean;
  setBusy: (on: boolean) => void; switched: (done: ProductUpdate, restarting: boolean) => void; setError: (message: string) => void;
}) {
  const [choice, setChoice] = useState<Channel | null>(null);
  const [preview, setPreview] = useState<ProductUpdate | null>(null);
  const following = update?.pinned ? null : update?.channel ?? null;

  const pick = async (channel: Channel) => {
    if (channel === following) { setChoice(null); setPreview(null); return; }
    setChoice(channel);
    setPreview(null);
    setError("");
    setBusy(true);
    try { setPreview(await checkUpdate(api, appVersion, channel)); }
    catch (error) { setChoice(null); setError(error instanceof Error ? error.message : String(error)); }
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
    } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  };
  const name = choice ? CHANNEL_LABEL[choice] : "";
  const installs = preview && (preview.runtime || preview.desktop);
  const shown = choice ?? following;
  return (
    <div className="r set" style={{ padding: "13px 14px", gap: 16, flexWrap: "wrap" }}>
      <div className="txt">
        <span className="label" style={{ fontSize: 14.5 }}>Release channel</span>
        <span className="sub wrap">
          {update?.pinned && !choice ? `Pinned to Tag ${update.current}. Choose a channel to follow one again.`
            : following === "edge" && !choice ? "Following edge, chosen in the terminal. Tag.app doesn't follow edge; choose a channel to update the app too."
            : isAppChannel(shown) ? CHANNEL_DETAIL[shown] : "The app and your Tags follow the same channel."}
        </span>
      </div>
      <div className="segc" role="radiogroup" aria-label="Release channel">
        {APP_CHANNELS.map((channel) => (
          <button key={channel} role="radio" aria-checked={shown === channel}
            disabled={busy || !update} onClick={() => void pick(channel)}>{CHANNEL_LABEL[channel]}</button>
        ))}
      </div>
      {choice && !preview && <span className="caption secondary row gap-6" style={{ flexBasis: "100%" }}><Spinner small />Checking {name}…</span>}
      {choice && preview && (
        <div className="ch-confirm">
          <span className="sub wrap" style={{ flex: 1, color: "var(--text)" }}>{previewText(name, preview)}</span>
          <button className="p-btn quiet sm" disabled={busy} onClick={() => { setChoice(null); setPreview(null); }}>Cancel</button>
          <button className="p-btn ink sm" disabled={busy} onClick={() => void confirm()}>
            {busy ? "Switching…" : installs ? "Switch and update" : "Switch"}
          </button>
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
  update: UpdateState;
  check: () => void;
  runUpdate: () => void;
  /** A channel switch finished; Home and Settings show the result. */
  switched: (done: ProductUpdate) => void;
  /** Opens Settings → AI & models. */
  openAI?: () => void;
}

export function Settings({ api, info, tags, close, update, check, runUpdate, switched, openAI }: SettingsProps) {
  const [login, setLogin] = useState(false);
  const [keepBusy, setKeepBusy] = useState(false);
  const [tagVersion, setTagVersion] = useState("");
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void api.openAtLogin().then(setLogin).catch(() => setLogin(false));
    void api.tag(["version", "--json"]).then((r) => {
      if (r.code === 0) setTagVersion(parseJSON<{ version: string }>(r.stdout).version);
    }).catch(() => {});
  }, [api]);

  // Opening Settings checks for an update.
  useEffect(() => {
    if (update.status === "idle" || update.status === "current" || update.status === "available") check();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const version = update.update?.current || tagVersion || info.version;
  const target = targetVersion(update);
  const row: Record<UpdateState["status"], [string, string, ReactNode]> = {
    idle: [`Tag ${version}`, "One version for the app and your Tags.", <Secondary small title="Check for updates" onClick={check} />],
    checking: [`Tag ${version}`, "Checking for updates…", <Busy text="Checking…" />],
    available: [`Tag ${target} is available`, `Updates the app and your Tags together, and restarts the app if needed. Your settings are kept. You have ${version}.`,
      <Primary small title="Update Tag" onClick={runUpdate} />],
    updating: [`Updating to ${target}…`, update.phase === "app" ? "Restarting the app to finish. Your Tags keep running." : "Updating your Tags. They restart on the new version.",
      <Spinner small label="Updating" />],
    failed: ["The update didn't finish", `Your settings are kept. Try again to finish updating${target ? ` to ${target}` : ""}.`,
      <Primary small title="Try again" onClick={runUpdate} />],
    done: [`Tag ${version}`, "Up to date. The app and your Tags are on the same version.", <UpToDate />],
    current: [`Tag ${version}`, "Up to date. The app and your Tags are on the same version.", <UpToDate />],
  };
  const [label, detail, control] = row[update.status];
  const art = update.status === "failed" ? puzzled : update.status === "updating" ? building : tagIcon;
  return (
    <>
      <CompactSky title="Settings" sub={`Tag ${version}`} back={close} />
      <div className="body">
        <div className="section">
          <div className="sec-head"><h3>General</h3></div>
          <div className="card">
            <SetRow label="Open Tag at login" detail="Keep Tag in the menu bar after you log in.">
              <Switch on={login} busy={false} label="Open Tag at login"
                onClick={() => void api.openAtLogin(!login).then(setLogin).catch((e) => setError(String(e)))} />
            </SetRow>
            <SetRow label="Keep Tags running"
              detail="Start the Tags you switched on after you log in, and restart any that stop. Works even when this app is closed.">
              <Switch on={tags.keepRunning} busy={keepBusy} label="Keep Tags running"
                onClick={async () => { setKeepBusy(true); await tags.setAutostart(!tags.keepRunning); setKeepBusy(false); }} />
            </SetRow>
          </div>
        </div>
        {openAI && (
          <div className="section">
            <div className="sec-head"><h3>AI &amp; models</h3></div>
            <div className="card"><AISummaryRow api={api} tags={tags} open={openAI} /></div>
          </div>
        )}
        <div className="section">
          <div className="sec-head"><h3>Updates</h3></div>
          <div className={update.status === "failed" ? "card upd-card failed" : "card upd-card"}>
            <div className="r set" style={{ padding: "13px 14px", gap: 16 }}>
              <img className="upd-icon" src={art} alt="" />
              <div className="txt"><span className="label" style={{ fontSize: 14.5 }}>{label}</span><span className="sub wrap">{detail}</span></div>
              {control}
            </div>
            {update.status === "updating" && <div className="upd-bar"><i style={{ width: update.phase === "app" ? "85%" : "40%" }} /></div>}
            <ReleaseChannel api={api} appVersion={info.version} update={update.update}
              busy={switching || update.status === "updating" || update.status === "checking"}
              setBusy={setSwitching} setError={setError}
              switched={(done, restarting) => { setTagVersion(done.version); switched(done); void tags.refresh(); if (!restarting) check(); }} />
          </div>
        </div>
        <div className="section">
          <div className="sec-head"><h3>About</h3></div>
          <div className="card" style={{ padding: "12px 14px", display: "flex", alignItems: "center", gap: 12 }}>
            <div className="txt"><span className="label" style={{ fontSize: 14.5 }}>Enjoying Tag?</span><CommunityLinks api={api} /></div>
          </div>
        </div>
        {(error || update.error || tags.error) && <ErrorLine>{error || update.error || tags.error}</ErrorLine>}
      </div>
    </>
  );
}

/** The Settings row that opens AI & models, with a summary for the main Tag. */
export function AISummaryRow({ api, tags, open }: { api: Bridge; tags: Tags; open: () => void }) {
  const list = configuredTags(tags.rows);
  const first = list.find((row) => row.main) ?? list[0];
  const [summary, setSummary] = useState("");
  useEffect(() => {
    if (!first) { setSummary("Set up a Tag to choose its model."); return; }
    let live = true;
    void api.tag(aiArgs(first.id, "--json")).then((r) => {
      if (!live || r.code !== 0) return;
      const report = parseStatus(r.stdout);
      setSummary(`${report.usable.length} of ${report.connections.length} connected · Default: ${report.default_model.label}`);
    }).catch(() => {});
    return () => { live = false; };
  }, [api, first?.id, first]);
  return (
    <div className="r set click" role="button" tabIndex={0} aria-label="Open AI and models" onClick={open}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }}
      style={{ padding: "13px 14px", gap: 16, borderRadius: "inherit" }}>
      <div className="txt"><span className="label" style={{ fontSize: 14.5 }}>AI &amp; models</span>
        <span className="sub wrap">{summary || "Connections and each Tag's default model"}</span></div>
      <span className="chev" aria-hidden="true"><Icon name="right" size={13} /></span>
    </div>
  );
}
