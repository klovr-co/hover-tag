// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// First run: install Tag with the installer bundled in this app.
import { useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import type { Bridge, Session } from "../lib/bridge";
import { fraction, initialInstall, installReducer, type StepState } from "../lib/install";
import { INSTALL_STEPS } from "../lib/protocol";
import welcomeArt from "../assets/art/welcome-maya.png";
import building from "../assets/art/tag-building.png";
import celebrate from "../assets/art/tag-celebrate.png";
import puzzled from "../assets/art/tag-puzzled.png";
import { ErrorLine, Icon, Primary, Quiet, Sky, Spinner, useCopied } from "./ui";

/** Keep startup in the same shell as setup, including a recoverable failure. */
export function Starting({ error, retry }: { error?: string; retry: () => void }) {
  return <>
    <Sky kind="hero" stars={50}>
      <img className="welcome-art" src={welcomeArt} alt="" />
    </Sky>
    <div className="body roomy">
      <div><h2>{error ? "Couldn't open Tag" : "Welcome to Tag"}</h2>
        <p className="lead">{error ? "Try again to load your Tags." : "Getting your Tags ready."}</p></div>
      {error ? <><ErrorLine>{error}</ErrorLine><div className="foot"><span className="spacer" />
        <Primary title="Try again" onClick={retry} /></div></>
        : <div className="row gap-10 secondary" role="status"><Spinner />Opening Tag…</div>}
    </div>
  </>;
}

/**
 * Every first-run install screen shares one canvas: the same sky and art, and a
 * panel of fixed height with its buttons pinned to the bottom. Moving from
 * Welcome to Installing to Installed changes only what's inside the panel, so
 * the window never resizes or jumps between them.
 */
function InstallCanvas({ title, lead, side, art, children, foot }: {
  title: ReactNode; lead: ReactNode; side?: ReactNode; art?: string; children: ReactNode; foot: ReactNode;
}) {
  return (
    <>
      <Sky kind="hero" stars={150}>
        <img className="welcome-art" src={welcomeArt} alt="Maya waving hello with her Water Tag" />
      </Sky>
      <div className="body roomy install">
        <div className="title-row">
          {art && <img className="sprite small" src={art} alt="" />}
          <div style={{ flex: 1, minWidth: 0 }}><div className="h2" style={{ fontSize: 26 }}>{title}</div><p className="lead">{lead}</p></div>
          {side}
        </div>
        <div className="install-main">{children}</div>
        <div className="foot">{foot}</div>
      </div>
    </>
  );
}

/** Replay ends with Done; first-run welcome offers installation and quitting. */
export function Welcome({ api, platform, install, preview }: { api: Bridge; platform: string } &
  ({ install: () => void; preview?: never } | { preview: () => void; install?: never })) {
  const machine = platform === "macos" ? "Mac" : "computer";
  return (
    <InstallCanvas title="Install Tag" lead="Multiplayer AI, right in Slack."
      foot={<>
        <span className="spacer" />
        {preview ? <Primary title="Done" onClick={preview} autoFocus /> : <>
          <Quiet title="Quit" onClick={() => void api.quit()} />
          <Primary title="Install Tag" onClick={install} autoFocus />
        </>}
      </>}>
      <div className="facts">
        <div className="fact"><span className="fi"><Icon name="user" size={16} /></span>
          <span><b>No administrator password.</b> Tag installs into your user folder and keeps your existing configuration.</span></div>
        <div className="fact"><span className="fi"><Icon name="disk" size={16} /></span><span>About <b>650 MB</b> of disk space</span></div>
        <div className="fact"><span className="fi"><Icon name="wifi" size={16} /></span><span>Keep this {machine} online until it finishes</span></div>
      </div>
    </InstallCanvas>
  );
}

const BLOCKS = 32;

function StepIcon({ state }: { state: StepState }) {
  if (state === "running") return <span className="sicon running"><span className="spin" /></span>;
  if (state === "done") return <span className="sicon done"><Icon name="check" size={12} /></span>;
  if (state === "failed") return <span className="sicon failed"><Icon name="bang" size={12} /></span>;
  return <span className="sicon pending" />;
}

export function Installing({ api, done, retry, cancel }: {
  api: Bridge; done: (command: string) => void; retry: () => void; cancel: () => void;
}) {
  const [state, dispatch] = useReducer(installReducer, initialInstall);
  const [showLog, setShowLog] = useState(false);
  const copy = useCopied();
  const [now, setNow] = useState(Date.now());
  const stepStart = useRef(Date.now());
  const session = useRef<Session | null>(null);

  useEffect(() => {
    let live = true;
    let owned: Session | null = null;
    void Promise.resolve().then(async () => {
      if (!live) return;
      const next = await api.install("", (line) => live && dispatch({ type: "line", line }),
        (code) => live && dispatch({ type: "exit", code }));
      if (!live) { next.stop(); return; }
      owned = next;
      session.current = next;
    }).catch((error) => {
      if (!live) return;
      dispatch({ type: "line", line: `Installation failed: ${String(error)}` });
      dispatch({ type: "exit", code: -1 });
    });
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => {
      live = false;
      clearInterval(timer);
      owned?.stop();
      if (session.current === owned) session.current = null;
    };
  }, [api]);
  useEffect(() => { stepStart.current = Date.now(); }, [state.current]);

  const failed = state.states.includes("failed");
  const [title, subtitle] = state.finished ? ["Tag is installed", "The tag command is ready. Next, set up your first Tag."]
    : failed ? ["Installation stopped", "You can safely try again."] : ["Installing Tag…", "This usually takes a few minutes."];
  const progress = state.finished ? 1 : fraction(state, (now - stepStart.current) / 1000);
  const percent = Math.round(progress * 100);
  return (
    <InstallCanvas title={title} lead={subtitle} art={state.finished ? celebrate : failed ? puzzled : building}
      side={<span className="install-pct">{failed ? "Stopped" : `${percent}%`}</span>}
      foot={<>
        <button className="link" onClick={() => setShowLog(!showLog)}>{showLog ? "Hide" : "Show"} details</button>
        {state.finished && state.version && <span className="ver selectable">Version {state.version}</span>}
        <span className="spacer" />
        {!state.finished && !failed && <Quiet title="Cancel" onClick={() => { session.current?.stop(); cancel(); }} />}
        {failed && <>
          <Quiet title={copy.label("Copy details")} icon={copy.copied ? undefined : "copy"} onClick={() => void api.copy(state.log).then(copy.mark, () => {})} />
          <Quiet title="Quit" onClick={() => void api.quit()} />
          <Primary title="Try again" onClick={retry} autoFocus />
        </>}
        {state.finished && <Primary title="Set up your first Tag" after="arrow" onClick={() => done(state.command)} autoFocus />}
      </>}>
      <div className={failed ? "pixbar install-bar bad" : "pixbar install-bar"} role="progressbar" aria-label="Installing Tag"
        aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        {Array.from({ length: BLOCKS }, (_, i) => <i key={i} className={i < Math.round(progress * BLOCKS) ? "on" : ""} />)}
      </div>
      {/* Details take the checklist's place, so showing them never grows the window. */}
      {showLog ? <pre className="logbox selectable" aria-label="Installer log">{state.log.trim() || "No output yet."}</pre> : (
        <div className="card steps">
          {INSTALL_STEPS.map((step, i) => {
            const s = state.finished ? "done" : state.states[i];
            return (
              <div key={step.step} className={`st ${s}`}>
                <StepIcon state={s} />
                <div style={{ minWidth: 0 }}>
                  <div className="stt">{step.title}</div>
                  {s === "running" && <div className="std">{step.detail}</div>}
                  {s === "failed" && <div className="std bad selectable">{state.failure}</div>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </InstallCanvas>
  );
}
