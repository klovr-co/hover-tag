// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// First run: install Tag with the installer bundled in this app.
import { useEffect, useReducer, useRef, useState } from "react";
import type { Bridge, Session } from "../lib/bridge";
import { fraction, initialInstall, installReducer, type StepState } from "../lib/install";
import { INSTALL_STEPS } from "../lib/protocol";
import { Header, Icon, Primary, Secondary, Spinner } from "./ui";

export function Welcome({ api, platform, install }: { api: Bridge; platform: string; install: () => void }) {
  const machine = platform === "macos" ? "Mac" : "computer";
  return (
    <>
      <Header title="Install Tag" subtitle="Your personal assistant, in Slack." />
      <div className="stack gap-14">
        <div>Tag installs into your user folder and does not need an administrator password. Your existing configuration is kept.</div>
        <div className="row gap-8"><Icon name="download" />About 650 MB of disk space</div>
        <div className="row gap-8"><Icon name="wifi" />Keep this {machine} online until it finishes</div>
        <div className="row gap-8">
          <div className="spacer" />
          <Secondary title="Quit" onClick={() => void api.quit()} />
          <Primary title="Install" onClick={install} autoFocus />
        </div>
      </div>
    </>
  );
}

function StepIcon({ state }: { state: StepState }) {
  if (state === "running") return <Spinner />;
  if (state === "done") return <span style={{ color: "var(--green)" }}><Icon name="check" size={16} /></span>;
  if (state === "failed") return <span style={{ color: "var(--red)" }}><Icon name="warning" size={16} /></span>;
  return <span style={{ width: 16, height: 16, borderRadius: "50%", border: "1.5px solid var(--tertiary)", display: "inline-block" }} />;
}

export function Installing({ api, done, retry }: { api: Bridge; done: (command: string) => void; retry: () => void }) {
  const [state, dispatch] = useReducer(installReducer, initialInstall);
  const [showLog, setShowLog] = useState(false);
  const [now, setNow] = useState(Date.now());
  const stepStart = useRef(Date.now());
  const session = useRef<Session | null>(null);

  useEffect(() => {
    let live = true;
    void api.install("", (line) => live && dispatch({ type: "line", line }),
      (code) => live && dispatch({ type: "exit", code })).then((s) => { session.current = s; });
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => { live = false; clearInterval(timer); };
  }, [api]);
  useEffect(() => { stepStart.current = Date.now(); }, [state.current]);

  const failed = state.states.includes("failed");
  const title = state.finished ? "Tag is installed" : failed ? "Installation stopped" : "Installing Tag…";
  const subtitle = state.finished ? "Ready to set up your first Tag."
    : failed ? "You can safely try again." : "This usually takes a few minutes.";
  const progress = fraction(state, (now - stepStart.current) / 1000);
  return (
    <>
      <Header title={title} subtitle={subtitle} />
      <div className="stack gap-14">
        <div className={failed ? "progress failed" : "progress"} role="progressbar" aria-valuenow={Math.round(progress * 100)}>
          <div style={{ width: `${progress * 100}%` }} />
        </div>
        <div className="well stack gap-10">
          {INSTALL_STEPS.map((step, i) => (
            <div key={step.step} className="row gap-12" style={{ alignItems: "flex-start" }}>
              <span style={{ width: 18, display: "grid", placeItems: "center" }}><StepIcon state={state.states[i]} /></span>
              <div className="stack gap-4">
                <span style={{ fontWeight: state.states[i] === "running" ? 600 : 400,
                  color: state.states[i] === "pending" ? "var(--secondary)" : undefined }}>{step.title}</span>
                {state.states[i] === "running" && <span className="caption secondary">{step.detail}</span>}
              </div>
            </div>
          ))}
        </div>
        {failed && <div className="error selectable">{state.failure}</div>}
        <details open={showLog} onToggle={(e) => setShowLog((e.target as HTMLDetailsElement).open)}>
          <summary>Details</summary>
          <pre className="log selectable">{state.log}</pre>
        </details>
        <div className="row gap-8">
          {state.version && !failed && state.finished && <span className="caption tertiary selectable">Version {state.version}</span>}
          <div className="spacer" />
          {!state.finished && !failed && <Secondary title="Cancel" onClick={() => { session.current?.stop(); void api.quit(); }} />}
          {failed && <>
            <Secondary title="Copy details" icon="copy" onClick={() => void api.copy(state.log)} />
            <Secondary title="Quit" onClick={() => void api.quit()} />
            <Primary title="Try again" onClick={retry} />
          </>}
          {state.finished && <Primary title="Continue" onClick={() => done(state.command)} autoFocus />}
        </div>
      </div>
    </>
  );
}
