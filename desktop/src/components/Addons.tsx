// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// The fixed places an add-on can fill, and the one "Connect …" line per
// provider in Settings. See docs/reference/addon-interface.md.
import { useEffect, useState } from "react";
import { AddonMount, useAddons, type FolderSlot, type ProviderState } from "../lib/addons/runtime";
import type { ButtonPlace } from "../lib/addons/manifest";
import type { ChannelTarget, Folder, TagTarget, ThreadTarget } from "../lib/addons/host";
import { CompactSky, Quiet, Secondary, Spinner, Switch } from "./ui";

/** Settings: one line per configured provider. Nothing is greyed out; a provider you haven't connected just offers to connect. */
export function AddonConnections() {
  const { providers } = useAddons();
  if (!providers.length) return null;
  return (
    <div className="section">
      <div className="card">{providers.map((s) => <ConnectRow key={s.provider.id} state={s} />)}</div>
    </div>
  );
}

function ConnectRow({ state }: { state: ProviderState }) {
  const { connect, cancel, disconnect } = useAddons();
  const { provider, status, account, manifest, error } = state;
  const id = provider.id;
  const label = status === "on" || status === "loading" ? provider.name : `Connect ${provider.name}`;
  const detail = status === "connecting" ? "Finish signing in in your browser. Tag.app installs the add-on when you're done."
    : status === "loading" ? "Checking the add-on's signature…"
      : status === "on" ? `Connected as ${account?.label ?? "you"}${manifest ? ` · add-on ${manifest.version}` : ""}`
        : error ?? provider.description ?? `Sign in once in your browser to add ${provider.name} to Tag.app.`;
  return (
    <div className="r set addon-connect" style={{ padding: "13px 14px", gap: 16 }}>
      <div className="txt"><span className="label" style={{ fontSize: 14.5 }}>{label}</span>
        <span className={error && status !== "connecting" ? "sub wrap bad" : "sub wrap"}>{detail}</span></div>
      {status === "off" && <Secondary small title="Connect" onClick={() => connect(id)} />}
      {status === "connecting" && <><Spinner small label="Waiting for the browser" /><Quiet small title="Cancel" onClick={() => cancel(id)} /></>}
      {status === "loading" && <Spinner small label="Loading" />}
      {status === "on" && <Quiet small title="Disconnect" onClick={() => disconnect(id)} />}
      {status === "error" && <><Secondary small title="Try again" onClick={() => connect(id)} /><Quiet small title="Disconnect" onClick={() => disconnect(id)} /></>}
    </div>
  );
}

/** Sidebar place: one button per add-on screen. */
export function AddonNav() {
  const { screens, openScreen } = useAddons();
  return <>{screens.map((s) => (
    <button key={s.key} className="sky-btn addon-nav" aria-label={s.title} title={s.title} onClick={() => openScreen(s.key)}>
      <span aria-hidden="true">{s.icon}</span>
    </button>
  ))}</>;
}

/** A whole add-on screen, opened from the sidebar. Shown next to the built-in screens, never instead of one. */
export function AddonScreen({ screen, back }: { screen: string; back: () => void }) {
  const { screens } = useAddons();
  const found = screens.find((s) => s.key === screen);
  return (
    <>
      <CompactSky title={found?.title ?? "Add-on"} back={back} />
      <div className="body addon-body">
        {found ? <AddonMount mount={found.mount} context={{}} />
          : <p className="lead">This screen belongs to an add-on that isn't connected now.</p>}
      </div>
    </>
  );
}

/** Named button place, such as a thread's actions. */
export function AddonButtons({ place, target, className = "activity-refresh" }: { place: ButtonPlace; target: ThreadTarget | TagTarget; className?: string }) {
  const { buttons, say } = useAddons();
  const here = buttons.filter((b) => b.place === place);
  return <>{here.map((b) => (
    <button key={b.key} className={`${className} addon-button`} onClick={() => {
      void Promise.resolve().then(() => b.run(target)).catch((e) => say(`${b.label} didn't work: ${e instanceof Error ? e.message : String(e)}`));
    }}>{b.label}</button>
  ))}</>;
}

/** The thread actions place, under a thread's details. */
export function AddonThreadActions({ tag, channel, thread }: { tag: string; channel: string; thread: string }) {
  const { buttons } = useAddons();
  if (!buttons.some((b) => b.place === "thread.actions")) return null;
  return <div className="activity-detail-footer"><AddonButtons place="thread.actions" target={{ tag, channel, thread_ts: thread }} /></div>;
}

/** The Tag actions place, in a Tag's header. */
export function AddonTagActions({ tag }: { tag: string }) {
  return <AddonButtons place="tag.actions" target={{ tag }} className="activity-refresh addon-tag-action" />;
}

/** Per-channel switch place, in a channel's row. */
export function AddonChannelSwitches({ target }: { target: ChannelTarget }) {
  const { switches } = useAddons();
  return <>{switches.map((s) => <ChannelSwitch key={s.key} slot={s} target={target} />)}</>;
}

function ChannelSwitch({ slot, target }: { slot: ReturnType<typeof useAddons>["switches"][number]; target: ChannelTarget }) {
  const { say } = useAddons();
  const [on, setOn] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const key = `${target.tag} ${target.channel.id}`;
  useEffect(() => {
    let live = true;
    void slot.get(target).then((value) => { if (live) setOn(!!value); }, () => { if (live) setOn(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slot, key]);
  if (on === null) return null;
  return (
    <span className="addon-switch" title={slot.detail}>
      <span className="meta">{slot.label}</span>
      <Switch on={on} busy={busy} label={`${slot.label} for #${target.channel.name ?? target.channel.id}`} onClick={async () => {
        setBusy(true);
        try { await slot.set(target, !on); setOn(!!(await slot.get(target))); }
        catch (e) { say(`${slot.label} didn't change: ${e instanceof Error ? e.message : String(e)}`); }
        finally { setBusy(false); }
      }} />
    </span>
  );
}

/** Knowledge tree place: add-on folders below the Tag's own, each opening into the add-on's panel. */
export function AddonKnowledgeFolders({ tag }: { tag: string }) {
  const { folders } = useAddons();
  const [rows, setRows] = useState<{ slot: FolderSlot; folder: Folder }[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void Promise.all(folders.map((slot) => slot.list({ tag }).then((list) => list.map((folder) => ({ slot, folder })), () => [])))
      .then((all) => { if (live) setRows(all.flat()); });
    return () => { live = false; };
  }, [folders, tag]);
  const picked = rows.find((r) => `${r.slot.key}/${r.folder.id}` === open) ?? rows[0];
  if (!rows.length) return <p className="meta">No shared folders for this Tag yet.</p>;
  return (
    <div className="addon-knowledge">
      <div className="card addon-tree" role="tree" aria-label="Shared folders">
        {rows.map((r) => {
          const id = `${r.slot.key}/${r.folder.id}`;
          return (
            <button key={id} role="treeitem" aria-selected={r === picked} className={r === picked ? "opt sel" : "opt"} onClick={() => setOpen(id)}>
              <span aria-hidden="true">{r.folder.icon ?? "📁"}</span>
              <span className="txt"><span className="label">{r.folder.title}</span>{r.folder.detail && <span className="sub">{r.folder.detail}</span>}</span>
            </button>
          );
        })}
      </div>
      {picked && <AddonMount key={`${picked.slot.key}/${picked.folder.id}`} mount={picked.slot.mount} context={{ tag, folder: picked.folder }} className="addon-mount addon-folder" />}
    </div>
  );
}
