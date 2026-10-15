// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// One update notice on Home that does the work in place: ready, required,
// updating, up to date, and Try again when an update stopped halfway. A release
// whose Tag.app builds aren't published yet still gets a notice, without a button.
import { useEffect, useRef, useState } from "react";
import { preparingText, targetVersion, type UpdateState } from "../lib/updates";
import { Icon, Spinner } from "./ui";

export interface Deferral { deferred: (version: string) => boolean; later: (version: string) => void; skip: (version: string) => void }

/** Later, with a small arrow beside it for Skip this version. */
function PutOff({ later, skip }: { later: () => void; skip: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  return (
    <span className="split" ref={ref}>
      <button className="p-btn quiet sm" onClick={later}>Later</button>
      <button className="p-btn quiet sm caret" aria-label="More ways to put this update off" aria-haspopup="menu" aria-expanded={open}
        onClick={() => setOpen(!open)}><Icon name="chevdown" size={12} /></button>
      {open && <div className="menu" role="menu"><button role="menuitem" onClick={skip}>Skip version</button></div>}
    </span>
  );
}

export function UpdateNotice({ state, outdated, run, settings, defer }: {
  state: UpdateState; outdated: boolean; run: () => void; settings: () => void; defer?: Deferral;
}) {
  const version = targetVersion(state);
  const current = state.update?.current ?? "";
  if (state.status === "updating") {
    const app = state.phase === "app";
    return (
      <div className="notice info" role="status">
        <span className="ic"><Icon name="up" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Updating Tag{version ? ` to ${version}` : ""}…</div>
          <div className="d">{app ? "Restarting the app to finish. Your Tags keep running." : state.phase === "runtime"
            ? "Updating your Tags. They restart on the new version." : "Checking that a complete update is available."}</div>
        </div>
        <div className="act" style={{ color: "var(--muted)" }}><Spinner small label="Updating" /></div>
        <span className="bar" style={{ width: app ? "85%" : "40%" }} />
      </div>
    );
  }
  if (state.status === "done") {
    return (
      <div className="notice ok" role="status">
        <span className="ic"><Icon name="check" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Tag is up to date</div>
          <div className="d">The app and your Tags are on {version}. Your settings were kept.</div>
        </div>
      </div>
    );
  }
  if (state.status === "failed") {
    return (
      <div className="notice" role="alert">
        <span className="ic"><Icon name="warn" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">The update didn't finish</div>
          <div className="d">Your settings are kept. Try again to finish updating{version ? ` to ${version}` : ""}.</div>
          {state.error && <div className="d selectable">{state.error}</div>}
        </div>
        <div className="act"><button className="p-btn ink sm" onClick={run}>Try again</button></div>
      </div>
    );
  }
  if (state.status === "idle" && state.error) {
    return (
      <div className="notice" role="alert">
        <span className="ic"><Icon name="warn" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Couldn't check for updates</div>
          <div className="d">This check made no changes. Your settings are kept.</div>
          <div className="d selectable">{state.error}</div>
          <button className="link" onClick={settings}>Release channel settings</button>
        </div>
        <div className="act"><button className="p-btn ink sm" onClick={run}>Try again</button></div>
      </div>
    );
  }
  const available = state.status === "available";
  if (outdated) {
    return (
      <div className="notice" role="alert">
        <span className="ic"><Icon name="up" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Update Tag to continue</div>
          <div className="d">{available ? `The app and your Tags need the same version. You have ${current}; ${version} is ready.`
            : "The app and your Tags need the same version."}</div>
        </div>
        <div className="act"><button className="p-btn ink sm" onClick={run}>Update Tag</button></div>
      </div>
    );
  }
  if (available && defer?.deferred(version)) return null;
  if (available) {
    return (
      <div className={defer ? "notice info menuable" : "notice info"}>
        <span className="ic"><Icon name="spark" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Tag {version} is ready</div>
          <div className="d">App and Tags update together.</div>
        </div>
        <div className="act">
          {/* One child, so the notice keeps its actions on the same row as the text. */}
          <span className="pair">
            <button className="p-btn soft sm" onClick={run}>Update Tag</button>
            {defer && <PutOff later={() => defer.later(version)} skip={() => defer.skip(version)} />}
          </span>
        </div>
      </div>
    );
  }
  const preparing = state.status === "current" ? state.update?.preparing : undefined;
  if (preparing) {
    return (
      <div className="notice info" role="status">
        <span className="ic"><Icon name="spark" size={16} /></span>
        <div style={{ flex: 1 }}>
          <div className="t">Tag {preparing} is on its way</div>
          <div className="d">{preparingText(preparing)}</div>
        </div>
        <div className="act" style={{ color: "var(--muted)" }}><Spinner small label="Preparing" /></div>
      </div>
    );
  }
  return null;
}
