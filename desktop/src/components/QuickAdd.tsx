// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Quick add: a dialog over Home that adds a Tag to a workspace you already use.
// It asks only for the name, description and picture, answers the rest of
// `tag add --json` itself (lib/setup.ts quickAnswer), and shows the creation
// in place. Anything it can't answer goes to the full setup.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { Bridge, Session } from "../lib/bridge";
import { useFresh, reducedMotion } from "../lib/motion";
import { initialSetup, quickAnswer, setupReducer } from "../lib/setup";
import { ProfileFields, useProfileDraft } from "./Connect";
import { ErrorLine, Icon, Primary, Spinner, source, tagIcon, WorkspaceMark } from "./ui";

/** Start a Tag; returns what went wrong, or "". */
type StartTag = (tag: string) => Promise<string>;

export interface QuickAddProps {
  api: Bridge;
  /** The Slack workspace's team ID and name. */
  workspace: string;
  workspaceName: string;
  workspaceIcon?: string | null;
  /** Its Enterprise Grid organization, when it's in one. */
  enterprise?: string | null;
  start: StartTag;
  /** The dialog is gone; `added` when a Tag was created. */
  close: (added: boolean) => void;
  /** Continue in the full setup for this draft Tag ("" when setup didn't name one), keeping what was typed. */
  moreOptions: (tag: string, profile: { name: string; description: string }) => void;
  /** Setup asked something the dialog doesn't answer: show it in the full setup. */
  handOff: (tag: string) => void;
}

type Phase = "form" | "creating" | "starting" | "done" | "failed";
type Leaving = "cancel" | "more" | "handoff" | null;

/** The steps the dialog shows while creating, in order; Tag reports all but the first and last. */
export const QUICK_STEPS = ["model", "create", "picture", "install", "connect", "start"] as const;

/** Which step is running: done before it, pending after. */
export function quickStep(phase: Phase, modelChosen: boolean, reported: string[]): number {
  if (phase === "starting") return QUICK_STEPS.indexOf("start");
  if (phase === "done") return QUICK_STEPS.length;
  if (!modelChosen) return 0;
  return Math.max(1, ...reported.map((step) => QUICK_STEPS.indexOf(step as typeof QUICK_STEPS[number])));
}

export function QuickAdd({ api, workspace, workspaceName, workspaceIcon, enterprise, start, close, moreOptions, handOff }: QuickAddProps) {
  const [state, dispatch] = useReducer(setupReducer, initialSetup);
  const session = useRef<Session | null>(null);
  const [phase, setPhase] = useState<Phase>("form");
  const [leaving, setLeaving] = useState<Leaving>(null);
  const [closing, setClosing] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [startError, setStartError] = useState("");
  const answered = useRef(new Set<string>());
  const q = state.question;
  const profileQuestion = q?.id === "profile" ? q : null;
  // Keep the last profile question so the fields stay put while the next one loads.
  const lastProfile = useRef(profileQuestion);
  if (profileQuestion) lastProfile.current = profileQuestion;
  const draft = useProfileDraft(lastProfile.current);

  useEffect(() => {
    let live = true;
    let owned: Session | null = null;
    // StrictMode replays effects before this microtask; only the surviving mount starts setup.
    void Promise.resolve().then(async () => {
      if (!live) return;
      const next = await api.setup(["add"],
        (line) => live && dispatch({ type: "line", line }),
        (code, stderr) => live && dispatch({ type: "exit", code, stderr }));
      if (!live) { next.stop(); return; }
      owned = next;
      session.current = next;
    }).catch((error) => { if (live) dispatch({ type: "exit", code: -1, stderr: String(error) }); });
    return () => {
      live = false;
      owned?.stop();
      if (session.current === owned) session.current = null;
    };
  }, [api]);

  const send = useCallback((answer: unknown) => {
    dispatch({ type: "answered" });
    session.current?.send({ answer });
  }, []);
  const sendInPlace = useCallback((answer: unknown) => session.current?.send({ answer }), []);
  const pause = useCallback(() => session.current?.send({ answer: null, pause: true }), []);

  // While creating, answer every question that has an obvious answer; anything else goes to the full setup.
  useEffect(() => {
    if (phase !== "creating" || !q || leaving) return;
    if (q.id === "profile") { setPhase("form"); return; } // Tag didn't accept the name: show why.
    const quick = answered.current.has(q.id) ? null : quickAnswer(q, workspace, enterprise);
    if (!quick) { setLeaving("handoff"); pause(); return; }
    answered.current.add(q.id);
    if (q.id === "default_model") {
      const index = typeof q.default === "number" ? q.default : -1;
      setModel(q.options?.[index] ?? null);
    }
    send(quick.answer);
  }, [phase, q, leaving, workspace, enterprise, send, pause]);

  // Setup ended: finish what the dialog was doing.
  // Without a Tag to continue, it says so and can be closed rather than waiting forever.
  const [lost, setLost] = useState(false);
  useEffect(() => {
    if (!state.outcome || leaving === "cancel") return;
    if (leaving === "more" || leaving === "handoff" || (state.outcome === "paused" && phase !== "form")) {
      if (!state.tag && leaving === "more") { moreOptions("", draft.answer()); return; }
      if (!state.tag) { setLost(true); setPhase("failed"); return; }
      if (leaving === "more") moreOptions(state.tag, draft.answer()); else handOff(state.tag);
      return;
    }
    if (state.outcome === "complete") setPhase((current) => current === "creating" ? "starting" : current);
    else setPhase("failed");
  }, [state.outcome, state.tag]); // eslint-disable-line react-hooks/exhaustive-deps

  // Created: start it once setup has let go of the Tag.
  const started = useRef(false);
  useEffect(() => {
    if (phase !== "starting" || !state.ended || !state.tag || started.current) return;
    started.current = true;
    void start(state.tag).then((failure) => { setStartError(failure); setPhase("done"); });
  }, [phase, state.ended, state.tag, start]);

  const busy = phase === "creating" || phase === "starting";
  const dismiss = useCallback(() => {
    if (busy || closing) return; // Slack is being changed: the dialog stays until it's done.
    const added = phase === "done";
    if (!added && phase === "form") { setLeaving("cancel"); pause(); }
    if (reducedMotion()) { close(added); return; }
    setClosing(true);
    setTimeout(() => close(added), 160);
  }, [busy, closing, phase, pause, close]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") dismiss(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dismiss]);

  const create = () => {
    if (draft.nameError || draft.busy || !profileQuestion) return;
    setPhase("creating");
    send(draft.answer());
  };
  const more = () => { setLeaving("more"); pause(); };

  const name = draft.name.trim() || "your Tag";
  const at = quickStep(phase, model !== null || answered.current.has("default_model"), (state.creating ?? []).map((s) => s.step));
  const labels: Record<string, string> = {
    model: "Load your models", create: "Create the Slack app", picture: "Add the picture",
    install: enterprise ? `Add it to ${workspaceName}` : `Install in ${workspaceName}`, connect: "Connect to this Mac", start: `Start ${name}`,
  };
  const modelFresh = useFresh(model);
  const picture = source(lastProfile.current?.preview, lastProfile.current?.preview_revision ?? lastProfile.current?.picture_label) ?? tagIcon;
  const title = phase === "done" ? (startError ? `${name} is in ${workspaceName}` : `${name} is ready`)
    : busy ? `Adding ${name}…` : `Add a Tag to ${workspaceName}`;

  return (
    <div className={closing ? "veil2 leaving" : "veil2"} onClick={dismiss}>
      <div className="dlg quick-dlg" role="dialog" aria-modal="true" aria-labelledby="quick-t" onClick={(e) => e.stopPropagation()}>
        <div className="quick-head">
          <WorkspaceMark label={workspaceName} icon={workspaceIcon ?? null} />
          <h3 id="quick-t">{title}</h3>
        </div>
        {phase === "form" && (
          <>
            <ProfileFields api={api} question={lastProfile.current} draft={draft} sendInPlace={sendInPlace} onEnter={create}
              disabled={!!leaving} />
            <div className="quick-model" role="note">
              <Icon name="spark" size={14} />
              <span>Model · <b className={modelFresh ? "fresh" : undefined}>{model ?? "your account's default"}</b>
                <span className="muted"> · change it in its Details any time</span></span>
            </div>
            {!lastProfile.current && !state.outcome && <div className="dlg-note" role="status"><Spinner small label="Getting ready" />Getting ready…</div>}
            {state.error && <ErrorLine>{state.error}</ErrorLine>}
            <div className="foot">
              <button className="link" onClick={more} disabled={!!leaving}>More options</button>
              <span className="spacer" />
              <button className="p-btn quiet" onClick={dismiss}>Cancel</button>
              <Primary title="Create in Slack" disabled={!profileQuestion || !!draft.nameError || draft.busy || !!leaving} onClick={create} />
            </div>
          </>
        )}
        {phase !== "form" && (
          <>
            <div className={phase === "done" ? "recap-top quick-made" : "recap-top"}>
              <img src={picture} alt="" />
              <div className="txt"><span className="name"><span className="nm">{name}</span></span>
                <span className="sub">{model ?? "Your default model"}</span></div>
              {phase === "done" && !startError && <span className="quick-check" aria-hidden="true"><Icon name="check" size={14} /></span>}
            </div>
            {phase !== "failed" && (
              <div className="steps" aria-label="Installation progress">
                {QUICK_STEPS.map((step, i) => {
                  const st = i < at ? "done" : i === at ? "running" : "pending";
                  return (
                    <div key={step} className={`st ${st}`} aria-current={st === "running" ? "step" : undefined}>
                      <span className={`sicon ${st}`}>{st === "done" ? <Icon name="check" size={12} /> : st === "running" ? <span className="spin" /> : null}</span>
                      <div className="stt">{labels[step]}</div>
                    </div>
                  );
                })}
              </div>
            )}
            {phase === "failed" && <ErrorLine>{lost ? "Setup stopped before it could finish. If it reached Slack, it's under Finish setting up on Home."
              : state.error || "Something went wrong. Your progress is saved."}</ErrorLine>}
            {phase === "done" && startError && <ErrorLine>{startError}</ErrorLine>}
            {phase === "done" && !startError && <p className="quick-hint">Invite it to a channel with <b>/invite @{name}</b>, or send it a direct message.</p>}
            {(phase === "done" || phase === "failed") && <div className="foot">
              <span className="spacer" />
              {phase === "failed" && state.tag && <button className="p-btn quiet" onClick={() => handOff(state.tag)}>Open full setup</button>}
              <Primary title="Done" onClick={dismiss} autoFocus />
            </div>}
          </>
        )}
      </div>
    </div>
  );
}
