// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Add a Tag: draws the questions Tag's own setup asks over JSON lines, in the
// onboarding order (Your Tag · AI · Workspace · Create · Channels). It holds no
// setup logic; every choice is an answer to `tag setup --json`.
import { useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import type { Bridge, Session } from "../lib/bridge";
import type { AIModels, AIStatus } from "../lib/ai";
import { findModel } from "../lib/ai";
import { fitEffort } from "../lib/model";
import type { SetupQuestion, SetupReady, SetupWorkspace } from "../lib/protocol";
import {
  EXISTING_FLOW, EXIT_OPTION, FLOW, heading, initialSetup, setupReducer, trackStep, type SetupState, type SignInStep,
} from "../lib/setup";
import { readActivity } from "../lib/watch";
import keyArt from "../assets/art/tag-key.png";
import puzzled from "../assets/art/tag-puzzled.png";
import { AgentMark, ModelMenu, ThinkingRow } from "./AI";
import { SlackSendDemo } from "./Slack";
import { Back, ErrorLine, fitText, Icon, OwnerMark, Primary, Quiet, Secondary, Sky, source, Spinner, tagIcon, WorkspaceMark } from "./ui";

interface Props {
  api: Bridge;
  /** Arguments for `tag … --json`: ["setup"], ["add"], or [tagId, "setup"]. */
  args: string[];
  done: () => void;
  /** Setup was cancelled; its progress is saved. */
  paused: () => void;
  openAI?: (resume: string[]) => void;
}

/** What a choose question's answer is: its stable ID when Tag gave them, else its index. */
const answerFor = (question: SetupQuestion, id: string) => {
  const index = question.option_ids?.indexOf(id) ?? -1;
  return index >= 0 ? id : (question.options ?? []).indexOf(id);
};

export function Connect({ api, args, done, paused, openAI }: Props) {
  const [state, dispatch] = useReducer(setupReducer, initialSetup);
  const session = useRef<Session | null>(null);
  const [attempt, setAttempt] = useState(0);
  const retryTag = useRef("");
  const openingAI = useRef(false);
  // Answers waiting for a question to come back, such as a workspace picked while an organization was open.
  const pending = useRef<{ id: string; answer: unknown } | null>(null);
  const approve = useRef<SetupQuestion | null>(null);

  useEffect(() => {
    let live = true;
    let owned: Session | null = null;
    // StrictMode replays effects before this microtask. Only the surviving
    // mount may start a process that creates a Tag on disk.
    void Promise.resolve().then(async () => {
      if (!live) return;
      const next = await api.setup(retryTag.current ? [retryTag.current, "setup"] : args,
        (line) => live && dispatch({ type: "line", line }),
        (code, stderr) => live && dispatch({ type: "exit", code, stderr }),
      );
      if (!live) { next.stop(); return; }
      owned = next;
      session.current = next;
    }).catch((error) => {
      if (live) dispatch({ type: "exit", code: -1, stderr: String(error) });
    });
    return () => {
      live = false;
      owned?.stop();
      if (session.current === owned) session.current = null;
    };
  }, [api, args, attempt]);

  useEffect(() => {
    if (openingAI.current && state.outcome === "paused") {
      openingAI.current = false;
      openAI?.(state.tag ? [state.tag, "setup"] : args);
    }
  }, [state.outcome, state.tag, openAI, args]);
  const q = state.question;
  if (q?.id === "approve_setup") approve.current = q;
  useEffect(() => {
    if (q && pending.current?.id === q.id) {
      const { answer } = pending.current;
      pending.current = null;
      dispatch({ type: "answered" });
      session.current?.send({ answer });
    }
  }, [q]);

  const send = (answer: unknown) => {
    dispatch({ type: "answered" });
    session.current?.send({ answer });
  };
  /** Answer without leaving the screen; Tag asks the same question again with what changed. */
  const sendInPlace = (answer: unknown) => session.current?.send({ answer });
  const back = () => {
    dispatch({ type: "answered" });
    session.current?.send({ back: true });
  };
  const backThen = (id: string, answer: unknown) => { pending.current = { id, answer }; back(); };
  const cancel = () => {
    if (state.signIn.step) session.current?.send({ cancel: true });
    session.current?.send({ answer: null, pause: true });
    paused();
  };
  if (state.outcome === "complete") return <Ready api={api} state={state} done={done} />;
  const picture = source(state.profile?.preview, state.profile?.revision);
  const body = (): ReactNode => {
    if (state.outcome === "paused") {
      return <FlowBody title="Progress saved" lead="You can finish setting up this Tag from Home at any time."
        foot={<><span className="spacer" /><Primary title="Done" onClick={done} autoFocus /></>} />;
    }
    if (state.outcome === "failed") {
      return (
        <FlowBody title="Setup stopped" lead={undefined} art={puzzled}
          foot={<><Quiet title="Back to Home" onClick={done} /><span className="spacer" />
            <Primary title="Try again" onClick={() => {
              retryTag.current = state.tag || retryTag.current;
              dispatch({ type: "restart" });
              setAttempt((value) => value + 1);
            }} autoFocus /></>}>
          <ErrorLine>{state.error || "Something went wrong. Your progress is saved."}</ErrorLine>
        </FlowBody>
      );
    }
    if (state.creating && approve.current) return <Create question={approve.current} state={state} send={send} picture={picture} />;
    if (!q) return <FlowBody title={state.lastQuestion ? "Getting the next step ready" : "Meet your new Tag"}
      lead={state.lastQuestion ? "Your choices are saved as you go." : "Give it a name, choose its AI, and connect it to Slack."}
      art={keyArt}>
      <div className="notice info" role="status"><Spinner /><span style={{ whiteSpace: "pre-line", overflowWrap: "anywhere", minWidth: 0 }}>{state.status}</span></div>
    </FlowBody>;
    switch (q.id) {
      case "profile": return <Meet key="profile" api={api} question={q} send={send} sendInPlace={sendInPlace}
        existing={() => { dispatch({ type: "existing" }); send("existing"); }} />;
      case "ai_connection": return <FlowBody title="Connect an AI in Settings" lead="Your AI accounts are shared by all Tags. Connect Codex or Claude, then return to choose this Tag's model."
        foot={<><BackIf question={q} back={back} /><span className="spacer" />
          {openAI ? <Primary title="Open Settings" onClick={() => { openingAI.current = true; session.current?.send({ answer: null, pause: true }); }} />
            : <Primary title="Check connections again" onClick={() => send("check")} />}</>} />;
      case "default_model": if (q.groups) return <ModelStep key={JSON.stringify(q.option_ids)} question={q} state={state} send={send} back={back} />; break;
      case "workspace": case "org_workspace": case "org_workspace_id":
        if (state.workspaces?.workspaces) return <Workspaces state={state} question={q} send={send} back={back} backThen={backThen}
          signIn={() => { dispatch({ type: "addWorkspace" }); send(answerFor(state.workspaces!, "sign_in")); }} />;
        break;
      case "approve_setup": if (q.recap) return <Create question={q} state={state} send={send} picture={picture} />; break;
      case "existing_app": case "app_id": case "app_checks": return <ExistingApp state={state} question={q} send={send} back={back} />;
      case "channels": if (q.channels) return <Channels question={q} name={state.profile?.name || q.tag_name || "Tag"} send={send} back={back} />; break;
    }
    if (q.kind === "slack_login") {
      return <SignIn api={api} question={q} state={state} setStep={(step) => dispatch({ type: "signIn", step })} send={send} />;
    }
    return q.kind === "choose" ? <Choose key={JSON.stringify(q)} question={q} send={send} back={back} />
      : q.kind === "multi" ? <Multi key={JSON.stringify(q)} question={q} send={send} back={back} />
      : q.kind === "confirm" ? <Confirm key={JSON.stringify(q)} question={q} send={send} back={back} />
      : <TextQuestion key={JSON.stringify(q)} question={q} send={send} back={back} />;
  };
  return (
    <>
      <FlowSky state={state} picture={picture} cancel={state.outcome ? undefined : cancel} />
      {body()}
      {state.error && !state.outcome && q?.id !== "profile" && <div style={{ margin: "-4px 24px 16px" }}><ErrorLine>{state.error}</ErrorLine></div>}
    </>
  );
}

/** The sky with the step track; the marker is the Tag being made, so it travels with you. */
function FlowSky({ state, picture, cancel }: { state: SetupState; picture: string | null; cancel?: () => void }) {
  const steps = state.existing ? EXISTING_FLOW : FLOW;
  const at = trackStep(state);
  const id = state.question?.id;
  const sub = state.existing ? "Use an existing Slack app" : at === 0 ? "Name it and give it a look"
    : id === "default_model" ? "Choose its model" : id === "ai_connection" ? "Connect it to an AI" : "Connect it to Slack";
  return (
    <Sky kind="flow" clouds="clear" stars={50}>
      {cancel && <div className="sky-top"><button className="sky-btn small" onClick={cancel}>Cancel</button></div>}
      <div className="flow-head"><h2>Add a Tag</h2><span>{sub}</span></div>
      <div className="track" aria-label="Setup progress">
        {steps.map((label, i) => (
          <div key={label} className={`tstep${i < at ? " done" : i === at ? " now" : ""}`} aria-current={i === at ? "step" : undefined}>
            <span className="tnode">{i === at ? <img src={picture ?? tagIcon} alt="" /> : <i />}</span>
            <span>{label}</span>
          </div>
        ))}
      </div>
    </Sky>
  );
}

function FlowBody({ title, lead, art, children, foot }: { title: ReactNode; lead?: ReactNode; art?: string; children?: ReactNode; foot?: ReactNode }) {
  return (
    <div className="body roomy">
      <div className="title-row">
        <div><div className="h2">{title}</div>{lead && <p className="lead">{lead}</p>}</div>
        {art && <img className="sprite" src={art} alt="" />}
      </div>
      {children}
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}

const BackIf = ({ question, back }: { question: SetupQuestion; back: () => void }) =>
  question.can_go_back ? <Back onClick={back} /> : null;

// ---- 1 · Meet your Tag ---------------------------------------------------------------

const EDIT_LEAD = "You’re still signed in. Done takes you back to Create.";

function Meet({ api, question, send, sendInPlace, existing }: {
  api: Bridge; question: SetupQuestion; send: (a: unknown) => void; sendInPlace: (a: unknown) => void; existing: () => void;
}) {
  const [name, setName] = useState(question.name ?? "");
  const [description, setDescription] = useState(question.description ?? "");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const preview = source(question.preview, question.preview_revision ?? question.picture_label);
  useEffect(() => { setBusy(false); }, [question]);
  useEffect(() => api.onFileDrop((paths) => { if (paths[0]) { setBusy(true); sendInPlace({ picture: paths[0] }); } }, setDragging), [api, sendInPlace]);
  const limit = question.name_limit ?? 35;
  const max = question.description_limit ?? 140;
  const nameError = !name.trim() ? "Give it a name." : name.trim().length > limit ? `Use ${limit} characters or fewer.` : "";
  const error = (name !== (question.name ?? "") || !question.error) ? nameError || question.error : question.error;
  const upload = async () => { const path = await api.pickImage(); if (path) { setBusy(true); sendInPlace({ picture: path }); } };
  const finish = () => { if (!nameError) send({ name: name.trim(), description: description.trim() }); };
  return (
    <div className="body roomy">
      <div>
        <div className="h2">{question.editing ? "Edit your Tag" : "Meet your new Tag"}</div>
        <p className="lead">{question.editing ? EDIT_LEAD : "Name it, give it a look, and say what it does. Nothing is created in Slack yet."}</p>
      </div>
      <div className="card mrow">
        <span className={dragging ? "avw2 dragging" : "avw2"}>
          <img key={preview ?? ""} className="pic fresh" src={preview ?? tagIcon} alt={`${name || "Tag"}'s picture`}
            style={question.picture === "custom" ? { background: "#fff" } : undefined} />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <label className="at-field">
            <span className="at-pill">@<span className="at-grow" data-v={name}>
              <input id="name" size={1} value={name} maxLength={40} autoFocus aria-label="Name, as people mention it in Slack"
                autoComplete="off" spellCheck={false} onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") finish(); }} />
            </span></span>
            <span className="slack-hint">in Slack</span>
          </label>
          <div className="desc-wrap">
            <textarea className="field desc" rows={2} maxLength={max} placeholder="One line on what it does" aria-label="Description" ref={fitText}
              value={description} onChange={(e) => { setDescription(e.target.value.replace(/[\r\n]+/g, " ")); fitText(e.currentTarget); }}
              onKeyDown={(e) => { if (e.key === "Enter") e.preventDefault(); }} />
            {description.length > max - 30 && <span className="desc-count">{max - description.length}</span>}
          </div>
          <div className="pic-acts">
            <button className="p-btn soft sm" disabled={busy} onClick={() => { setBusy(true); sendInPlace("shuffle"); }}><Icon name="dice" size={16} />Shuffle picture</button>
            <button className="p-btn quiet sm" disabled={busy} onClick={() => void upload()}><Icon name="upload" size={16} />Upload your own</button>
          </div>
        </div>
      </div>
      {error && <div className="field-err" role="alert"><Icon name="warn" /><span>{error}</span></div>}
      <div className="foot">
        {question.can_use_existing && !question.editing && <button className="link" onClick={existing}>Use an existing app</button>}
        <span className="spacer" />
        {question.editing ? <Primary title="Done" disabled={!!nameError} onClick={finish} />
          : <Primary title="Continue" after="arrow" disabled={!!nameError || busy} onClick={finish} />}
      </div>
    </div>
  );
}

// ---- 2 · AI ----------------------------------------------------------------------------

/** The Tag's default model, from every connected account; the model picks the agent. */
function ModelStep({ question, state, send, back }: {
  question: SetupQuestion; state: SetupState; send: (a: unknown) => void; back: () => void;
}) {
  const ids = question.option_ids ?? [];
  const groups = question.groups ?? [];
  const preset = typeof question.default === "number" ? ids[question.default] : null;
  const [value, setValue] = useState<string | null>(preset);
  const [effort, setEffort] = useState<string | null>(question.default_effort ?? null);
  const entry = value ? findModel(groups, value)?.entry ?? null : null;
  const level = fitEffort(entry, effort);
  const signingIn = !!state.signIn.step;
  const offered = !!value && !!findModel(groups, value);
  const tag = question.tag_name || state.profile?.name || "Tag";
  const connections = question.connections ?? [];
  const models = { groups, default: null, unavailable: [], suggested: null } as unknown as AIModels;
  const report = { connections, default_model: { value: preset ?? "", label: preset?.split(":")[1] ?? "" } } as unknown as AIStatus;
  return (
    <FlowBody title={`Choose ${tag}'s model`} lead="Model and thinking level apply to every request to this Tag. Change them any time in its Details tab."
      foot={<>{!signingIn && <BackIf question={question} back={back} />}<span className="spacer" />
        <Primary title="Continue" after="arrow"
          disabled={!offered || signingIn} onClick={() => send(question.supports_effort ? { value, effort: level ?? "default" } : value)} /></>}>
      <div className="card mcard">
        <ModelMenu models={models} report={report} value={value} inline onChange={(next) => {
          setEffort(fitEffort(findModel(groups, next)?.entry, level));
          setValue(next);
        }} />
        {question.supports_effort && <ThinkingRow entry={entry} value={level} onChange={setEffort} />}
        {value && !offered && (
          <div className="mwarn" role="status"><Icon name="warn" /><span>{value.split(":")[1] ?? value} isn't available from your connected accounts. Pick another.</span></div>
        )}

      </div>
    </FlowBody>
  );
}

// ---- Sign in to Slack (only when needed) ---------------------------------------------------

function SignIn({ api, question, state, setStep, send }: {
  api: Bridge; question: SetupQuestion; state: SetupState; setStep: (s: SignInStep) => void; send: (a: unknown) => void;
}) {
  const [code, setCode] = useState("");
  const step = state.signInStep;
  const line = question.sign_in_line ?? "";
  const items = [
    { title: "Copy your sign-in line", detail: "A one-time line that tells Slack this computer is yours.",
      act: <div className="ticket"><span className="mono">{line}</span>
        <Primary small title="Copy" icon="copy" autoFocus onClick={() => { void api.copy(line); setStep(1); }} /></div>,
      redo: <button className="link redo" onClick={() => setStep(0)}>Copy again</button> },
    { title: "Send it in Slack, then click Confirm",
      detail: "Paste it into any message box in the workspace you want, and send it. Slack asks you to confirm.",
      act: <><SlackSendDemo line={line} /><div className="cb">
        <Secondary small title="Open Slack" icon="external" onClick={() => void api.open("slack://open")} />
        <Primary small title="I clicked Confirm" autoFocus onClick={() => setStep(2)} /></div></>,
      redo: <button className="link redo" onClick={() => setStep(1)}>Show me again</button> },
    { title: "Paste the code from Slack", detail: "After you confirm, Slack shows a short code.",
      act: <><div className="field-wrap">
        <input className="field code" placeholder="Code from Slack" value={code} autoFocus aria-label="Code from Slack" autoComplete="off" spellCheck={false}
          onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && code.trim()) send(code.trim()); }} />
        <button className="p-btn soft sm in-btn" onClick={() => void api.paste().then((t) => setCode(t.trim()))}><Icon name="paste" />Paste</button>
      </div><div className="cb"><Primary small title="Connect" disabled={!code.trim()} onClick={() => send(code.trim())} /></div></> },
  ];
  return (
    <FlowBody title={state.addingWorkspace ? "Sign in to another workspace" : `Sign in so ${state.profile?.name || "your Tag"} can join Slack`}
      lead="Three quick steps. You'll switch to Slack and back once." art={keyArt}>
      <div className="card checklist">
        {items.map((item, i) => {
          const st = i < step ? "done" : i === step ? "now" : "pending";
          return (
            <div key={item.title} className={`ck ${st}`} aria-current={st === "now" ? "step" : undefined}>
              <span className="num">{st === "done" ? <Icon name="check" size={12} /> : i + 1}</span>
              <div><div className="ct">{item.title}</div>{st === "now" && <div className="cd">{item.detail}</div>}</div>
              {st === "done" && item.redo ? item.redo : <span />}
              {st === "now" && <div className="ca">{item.act}</div>}
            </div>
          );
        })}
      </div>
    </FlowBody>
  );
}

// ---- 3 · Workspace -------------------------------------------------------------------------

const T_ID = /\b(T[A-Z0-9]{6,})\b/;
const E_ID = /\bE[A-Z0-9]{6,}\b/;
export function workspaceIdError(text: string) {
  if (!text.trim() || T_ID.test(text)) return "";
  return E_ID.test(text) ? "That's the organization ID. Use the workspace's, which starts with T." : "No workspace ID found. It starts with T.";
}

function Workspaces({ state, question, send, back, backThen, signIn }: {
  state: SetupState; question: SetupQuestion; send: (a: unknown) => void; back: () => void;
  backThen: (id: string, answer: unknown) => void; signIn: () => void;
}) {
  const list = state.workspaces!;
  const rows = list.workspaces ?? [];
  const inOrg = question.id !== "workspace";
  const [manual, setManual] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const pick = (w: SetupWorkspace) => {
    if (inOrg) {
      if (w.kind === "organization" && w.id === question.organization?.id) { back(); return; }
      backThen("workspace", answerFor(list, w.id));
      return;
    }
    if (w.kind === "organization") setLoading(w.id);
    send(answerFor(list, w.id));
  };
  const tagName = state.profile?.name || "your Tag";
  const lead = state.existing ? "Pick the workspace your app is in." : `You're already signed in to these. You'll own ${tagName}.`;
  const orgRows = question.id === "org_workspace" ? question.workspaces ?? [] : [];
  const typed = (manual.match(T_ID) ?? [])[1] ?? "";
  return (
    <FlowBody title="Which workspace?" lead={lead}
      foot={<>{(question.can_go_back || inOrg) && <Back onClick={back} />}</>}>
      <div className="card" role="listbox" aria-label="Workspaces">
        {rows.map((w) => {
          const org = w.kind === "organization";
          const open = org && inOrg && question.organization?.id === w.id;
          return (
            <div key={w.id}>
              <button className="opt" role="option" aria-selected={open} aria-expanded={org ? open : undefined} onClick={() => pick(w)}>
                <WorkspaceMark label={w.name} icon={null} big />
                <span className="txt">
                  <span className="label">{w.name}{org && <span className="kind">Organization</span>}</span>
                  <span className="sub">{org ? "Pick a workspace inside it next" : w.user_name ? `Signed in as @${w.user_name}` : "Signed in"}</span>
                </span>
                {loading === w.id ? <span className="spin" style={{ color: "var(--muted)" }} />
                  : <span className={org ? `chev turn${open ? " open" : ""}` : "chev"}><Icon name="right" size={13} /></span>}
              </button>
              {open && orgRows.map((sub) => (
                <button key={sub.id} className="opt sub-opt" onClick={() => send(answerFor(question, sub.id))}>
                  <WorkspaceMark label={sub.name} icon={null} />
                  <span className="label" style={{ flex: 1, fontSize: 14.5 }}>{sub.name}</span><span className="chev"><Icon name="right" size={13} /></span>
                </button>
              ))}
              {open && question.id === "org_workspace" && (
                <button className="more-ws" onClick={() => send(answerFor(question, "manual"))}>Workspace not listed?</button>
              )}
              {open && question.id === "org_workspace_id" && (
                <div className="sub-manual">
                  <div className="field-wrap">
                    <input className="field" autoFocus placeholder="Workspace address or ID (T…)" value={manual} autoComplete="off" spellCheck={false}
                      onChange={(e) => setManual(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && typed) send(manual.trim()); }} />
                    <button className="p-btn ink sm in-btn" disabled={!typed} onClick={() => send(manual.trim())}>Use it</button>
                  </div>
                  {workspaceIdError(manual) && <div className="field-err"><Icon name="warn" /><span>{workspaceIdError(manual)}</span></div>}
                </div>
              )}
            </div>
          );
        })}
        {!inOrg && (
          <button className="opt" onClick={signIn}>
            <span className="ws big add-ws"><Icon name="plus" /></span>
            <span className="txt"><span className="label" style={{ color: "var(--link)" }}>Sign in to another workspace</span><span className="sub">Or another organization</span></span>
          </button>
        )}
      </div>
    </FlowBody>
  );
}

// ---- 4 · Create ------------------------------------------------------------------------------

const CREATE_STEPS = ["create", "picture", "install", "connect"];

function Create({ question, state, send, picture }: { question: SetupQuestion; state: SetupState; send: (a: unknown) => void; picture: string | null }) {
  const recap = question.recap!;
  const running = !!state.creating;
  const where = recap.workspace.organization ? recap.workspace.name : recap.workspace.name;
  const reported = state.creating ?? [];
  const labels: Record<string, string> = {
    create: "Create the Slack app", picture: "Add the picture",
    install: recap.workspace.organization ? `Add it to ${recap.workspace.name}` : `Install in ${recap.workspace.name}`, connect: "Connect to this Mac",
  };
  const at = Math.max(...reported.map((s) => CREATE_STEPS.indexOf(s.step)), 0);
  const pick = (id: string) => send(answerFor(question, id));
  return (
    <FlowBody title={running ? "Creating it in Slack…" : `Ready to create it in ${where}?`}
      lead={running ? undefined : "Slack creates the app with this name and picture, then installs it."}
      foot={running
        ? <span className="meta">{recap.approval ? "If an organization admin needs to approve, setup pauses here and picks up where it left off."
          : "Takes about a minute. Slack may ask a workspace admin to approve."}</span>
        : <>{question.can_go_back && <Back onClick={() => pick("back")} />}<span className="spacer" /><Primary title="Create in Slack" onClick={() => pick("create")} autoFocus /></>}>
      <div className="card">
        <div className="recap-top">
          <img src={source(recap.picture, recap.picture_revision ?? state.profile?.revision) ?? picture ?? tagIcon} alt="" />
          <div className="txt"><span className="name"><span className="nm">{recap.name}</span></span>
            <span className="sub wrap">{recap.description || "New Slack app"}</span></div>
          <button className="link" disabled={running} onClick={() => pick("edit")}>Edit</button>
        </div>
        <dl className="summary">
          <dt>Workspace</dt>
          <dd><WorkspaceMark label={recap.workspace.name} icon={recap.workspace.icon ?? null} />{recap.workspace.name}
            {recap.workspace.organization && <span className="kind">in {recap.workspace.organization.name}</span>}</dd>
          <dt>Owner</dt>
          <dd><span className="owner"><OwnerMark icon={recap.owner.icon} />You{recap.owner.name ? ` · @${recap.owner.name}` : ""}</span></dd>
          {recap.ai && <><dt>AI</dt><dd><AgentMark backend={recap.ai.backend} size={20} />{recap.ai.backend_name} · {recap.ai.label}
            <button className="link" disabled={running} onClick={() => pick("edit_ai")}>Edit</button></dd></>}
          <dt>Who can ask it</dt>
          <dd>Only you <span style={{ fontWeight: 400, color: "var(--muted)" }}>· people in the channel see its replies</span></dd>
          {recap.approval && <><dt>Approval</dt><dd style={{ fontWeight: 500, color: "var(--muted)" }}>An org admin may need to approve. Setup waits and resumes.</dd></>}
        </dl>
        {running && (
          <div className="steps" style={{ borderTop: "1px solid var(--line)" }}>
            {CREATE_STEPS.map((step, i) => {
              const st = i < at ? "done" : i === at ? "running" : "pending";
              return (
                <div key={step} className={`st ${st}`}>
                  <span className={`sicon ${st}`}>{st === "done" ? <Icon name="check" size={12} /> : st === "running" ? <span className="spin" /> : null}</span>
                  <div className="stt">{labels[step]}</div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </FlowBody>
  );
}

// ---- 4b · Existing app ------------------------------------------------------------------

const APP_SOURCE: Record<string, string> = { linked: "Made with Tag, not in use", cli: "From the Slack CLI", tag: "Used by another Tag" };

function ExistingApp({ state, question, send, back }: { state: SetupState; question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [list, setList] = useState<SetupQuestion | null>(question.id === "existing_app" ? question : null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  useEffect(() => { if (question.id === "existing_app") setList(question); }, [question]);
  const apps = (list ?? question).apps ?? [];
  const checks = question.id === "app_checks" ? question.checks ?? [] : null;
  const ok = checks?.filter((c) => c.ok) ?? [];
  const bad = checks?.filter((c) => !c.ok) ?? [];
  const has = (id: string) => (question.option_ids ?? []).includes(id);
  const pick = (id: string) => { setChosen(id); send(answerFor(question, id)); };
  const appName = apps.find((a) => a.id === chosen)?.name ?? "your app";
  const id = typed.match(/\b(A[A-Z0-9]{6,})\b/)?.[1] ?? "";
  const checkRows = checks && (
    <div className="ex-checks">
      <div className="st"><span className="sicon done"><Icon name="check" size={12} /></span>
        <div><div className="stt">{bad.length ? "Everything else is set" : "Ready to go"}</div>{ok.length > 0 && <div className="std">{ok.map((c) => c.label).join(" · ")}</div>}</div></div>
      {bad.map((c) => (
        <div key={c.label} className="st"><span className="sicon failed"><Icon name="bang" size={12} /></span>
          <div><div className="stt">{c.label}</div>{c.detail && <div className="std">{c.detail}</div>}</div></div>
      ))}
    </div>
  );
  return (
    <FlowBody title="Which app?" lead={`Your Slack apps in ${state.workspaces?.workspaces?.find((w) => w.kind !== "organization")?.name ?? "this workspace"}.`}
      foot={<><Back onClick={() => (question.id === "existing_app" ? back() : send(answerFor(question, "back")))} />
        {checks && has("manual") && <button className="link" onClick={() => send(answerFor(question, "manual"))}>I'll do it myself</button>}
        <span className="spacer" />
        <Primary title="Connect" disabled={!(checks && has("connect"))} onClick={() => send(answerFor(question, "connect"))} /></>}>
      <div className="card" role="listbox" aria-label="Slack apps">
        {apps.map((app) => app.used_by ? (
          <div key={app.id} className="opt used" aria-disabled="true">
            <WorkspaceMark label={app.name} icon={null} big />
            <span className="txt"><span className="label">{app.name}</span><span className="sub">Already used by your Tag “{app.used_by}”</span></span>
          </div>
        ) : (
          <div key={app.id}>
            <button className="opt" role="option" aria-selected={chosen === app.id} disabled={question.id !== "existing_app" && chosen !== app.id}
              onClick={() => question.id === "existing_app" && pick(app.id)}>
              <WorkspaceMark label={app.name} icon={null} big />
              <span className="txt"><span className="label">{app.name}</span><span className="sub">{APP_SOURCE[app.source] ?? app.source}</span></span>
              <span className={chosen === app.id ? "radio on" : "radio"} />
            </button>
            {chosen === app.id && (checks ? checkRows : question.id !== "existing_app" && (
              <div className="opt sub-opt"><span className="spin" style={{ color: "var(--muted)" }} /><span className="meta">Checking {app.name}'s settings…</span></div>
            ))}
          </div>
        ))}
        {question.id === "app_id" ? (
          <div className="sub-manual" style={{ paddingLeft: 14 }}>
            <div className="field-wrap">
              <input className="field" autoFocus placeholder="App link or ID" value={typed} autoComplete="off" spellCheck={false}
                onChange={(e) => setTyped(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && id) { setChosen(id); send(typed.trim()); } }} />
              <button className="p-btn soft sm in-btn" disabled={!id} onClick={() => { setChosen(id); send(typed.trim()); }}>Use it</button>
            </div>
            {typed.trim() && !id ? <div className="field-err"><Icon name="warn" /><span>That doesn't look like an app link. App IDs start with A.</span></div>
              : <div className="hintline">Paste the link from api.slack.com/apps.</div>}
          </div>
        ) : question.id === "existing_app" && (question.option_ids ?? []).includes("other") && (
          <button className="more-ws" style={{ paddingLeft: 14 }} onClick={() => pick("other")}>Use a different app</button>
        )}
        {checks && chosen && !apps.some((a) => a.id === chosen) && checkRows}
      </div>
      {checks && has("update") && (
        <div className="notice">
          <span className="ic"><Icon name="warn" size={16} /></span>
          <div style={{ flex: 1 }}><div className="t">Tag can add the missing permissions</div>
            <div className="d">Nothing else changes. Slack will ask you to reinstall {appName}{state.workspaces?.workspaces?.some((w) => w.kind === "organization") ? ", and an organization admin may need to approve" : ""}.</div></div>
          <div className="act"><Primary small title="Add them" onClick={() => send(answerFor(question, "update"))} /></div>
        </div>
      )}
    </FlowBody>
  );
}

// ---- 5 · Channels --------------------------------------------------------------------------

function Channels({ question, name, send, back }: { question: SetupQuestion; name: string; send: (a: unknown) => void; back: () => void }) {
  const all = (question.channels ?? []).map((c, i) => ({ ...c, i }));
  const [picked, setPicked] = useState(new Set((question.selected ?? []).filter((i) => !all[i]?.member)));
  const [query, setQuery] = useState("");
  const joined = all.filter((c) => c.member);
  const chosen = all.filter((c) => !c.member && picked.has(c.i));
  const q = query.trim().toLowerCase().replace(/^#/, "");
  const shown = q ? all.filter((c) => c.name.includes(q)).sort((x, y) => Number(y.name.startsWith(q)) - Number(x.name.startsWith(q)) || x.name.localeCompare(y.name))
    : [...joined, ...chosen, ...all.filter((c) => !c.member && !picked.has(c.i))];
  const flip = (i: number) => setPicked((set) => { const next = new Set(set); if (next.has(i)) next.delete(i); else next.add(i); return next; });
  const total = joined.length + chosen.length;
  const answer = () => send([...joined.map((c) => c.i), ...chosen.map((c) => c.i)].sort((a, b) => a - b));
  const lock = <Icon name="lock" size={13} />;
  return (
    <FlowBody title={`Where should ${name} start?`} lead="It answers and remembers conversations in these channels. Later invitations are added automatically."
      foot={<>{question.can_go_back && <Back onClick={back} />}
        {question.allow_empty && <button className="link" onClick={() => send([])}>Skip for now</button>}<span className="spacer" />
        <Primary title={`Continue with ${total} channel${total === 1 ? "" : "s"}`} disabled={!total} onClick={answer} /></>}>
      {total > 0 && (
        <div className="chan-chips">
          {[...joined, ...chosen].map((c) => (
            <span key={c.id} className="chan-chip">{c.private ? lock : "#"}{c.name}
              {!c.member && <button onClick={() => flip(c.i)} aria-label={`Remove ${c.name}`}>×</button>}</span>
          ))}
        </div>
      )}
      <div className="card">
        <label className="search"><Icon name="search" size={16} />
          <input placeholder={`Search ${all.length} channels`} value={query} aria-label="Search channels" autoComplete="off" spellCheck={false}
            onChange={(e) => setQuery(e.target.value)} />
          {query && <button className="link" onClick={() => setQuery("")}>Clear</button>}
        </label>
        <div className="chan-list">
          {shown.length ? shown.map((c) => c.member ? (
            <div key={c.id} className="opt" style={{ cursor: "default" }}>
              <span className="hash">{c.private ? lock : "#"}</span>
              <span className="txt"><span className="label">{c.name}</span><span className="sub">{c.private ? "Private · " : ""}Already in this channel</span></span>
              <span className="box on" style={{ opacity: 0.55 }}><Icon name="check" size={12} /></span>
            </div>
          ) : (
            <button key={c.id} className="opt" role="checkbox" aria-checked={picked.has(c.i)} onClick={() => flip(c.i)}>
              <span className="hash">#</span>
              <span className="txt"><span className="label">{c.name}</span><span className="sub">Public{picked.has(c.i) ? ` · ${name} joins it` : ""}</span></span>
              <span className={picked.has(c.i) ? "box on" : "box"}>{picked.has(c.i) && <Icon name="check" size={12} />}</span>
            </button>
          )) : (
            <div className="meta" style={{ padding: "18px 16px", lineHeight: 1.5 }}>No channel matches “{query.trim()}”. If it's private, invite {name} after
              setup: type <span className="mono">/invite @{name}</span> in that channel.</div>
          )}
        </div>
        <div className="opt private-row">
          <span className="hash">{lock}</span>
          <span className="txt"><span className="label">Private channels</span>
            <span className="sub wrap">After setup, type <span className="mono">/invite @{name}</span> in the channel.</span></span>
        </div>
      </div>
    </FlowBody>
  );
}

// ---- Ready ------------------------------------------------------------------------------------

const TRY_PROMPTS = ["What can you help me with?", "Summarize the last week here", "Draft a launch checklist with owners"];
const CONFETTI: [number, number, string][] = [[60, 40, "#ffd84d"], [96, 86, "#ff7aa8"], [140, 30, "#7ee08a"], [190, 70, "#fff"], [330, 36, "#ffd84d"],
  [372, 84, "#7ee08a"], [420, 46, "#ff7aa8"], [462, 96, "#fff"], [230, 24, "#fff"], [296, 100, "#ff7aa8"]];

/** Where a first message can go: a direct message with the Tag, or a channel it's in. */
export function tryPlaces(ready: SetupReady | null) {
  return [{ id: "dm", label: "Direct message", dm: true }, ...(ready?.channels ?? []).map((c) => ({ id: c.id, label: `#${c.name}`, dm: false }))];
}

export function slackLink(ready: SetupReady, place: string) {
  return place === "dm" ? `slack://app?team=${encodeURIComponent(ready.team)}&id=${encodeURIComponent(ready.app_id)}&tab=messages`
    : `slack://channel?team=${encodeURIComponent(ready.team)}&id=${encodeURIComponent(place)}`;
}

function Ready({ api, state, done }: { api: Bridge; state: SetupState; done: () => void }) {
  const name = state.profile?.name || "your Tag";
  const ready = state.ready;
  const places = tryPlaces(ready);
  const [where, setWhere] = useState("dm");
  const [prompt, setPrompt] = useState(0);
  const [step, setStep] = useState<"idle" | "starting" | "waiting" | "done" | "failed">("idle");
  const [error, setError] = useState("");
  const place = places.find((p) => p.id === where) ?? places[0];
  const text = `${place.dm ? "" : `@${name} `}${TRY_PROMPTS[prompt]}`;
  const picture = source(state.profile?.preview, state.profile?.revision);
  // Ticks only once Tag records a reply after Start; nothing is made up.
  useEffect(() => {
    if (step !== "waiting" || !state.tag) return;
    const since = Date.now();
    const timer = setInterval(() => void readActivity(api, state.tag).then((items) => {
      if (items?.some((item) => item.kind === "replied" && Date.parse(item.at) >= since - 5000)) setStep("done");
    }).catch(() => {}), 5000);
    return () => clearInterval(timer);
  }, [api, state.tag, step]);
  const start = async () => {
    setStep("starting");
    setError("");
    const result = await api.tag(state.tag ? [state.tag, "start"] : ["start"]);
    if (result.code !== 0) { setStep("failed"); setError((result.stderr || result.stdout).trim().split("\n").pop() ?? ""); return; }
    await api.copy(text);
    if (ready) void api.open(slackLink(ready, place.id));
    setStep("waiting");
  };
  return (
    <>
      <Sky kind="ready" stars={90}>
        {CONFETTI.map(([left, top, background], i) => <span key={i} className="confetti" style={{ left, top, background }} />)}
        <img className="big-av" src={picture ?? tagIcon} alt="" />
      </Sky>
      <div className="body roomy">
        <div style={{ textAlign: "center" }}>
          <div className="eyebrow" style={{ color: "var(--green)" }}>Setup complete</div>
          <div className="h2" style={{ fontSize: 26 }}>Say hi to {name}</div>
          <p className="lead">Start it, then send this in Slack. We copy it for you.</p>
          {ready?.ai && <p className="ai-line"><AgentMark backend={ready.ai.backend} size={18} /><span><b>{ready.ai.backend_name} connected</b></span></p>}
        </div>
        <div className="thread try-thread">
          <div className="thread-h">{place.dm ? "Direct message" : "Thread"}
            <span className="place-wrap"><span className="place-now">{place.dm ? `DM with ${name}` : place.label}</span>
              <select className="place-sel" aria-label="Where to try it" value={place.id} disabled={step !== "idle"} onChange={(e) => setWhere(e.target.value)}>
                {places.map((p) => <option key={p.id} value={p.id}>{p.dm ? `DM with ${name}` : p.label}</option>)}
              </select>{step === "idle" && <Icon name="chevdown" size={10} />}</span>
          </div>
          <div className="smsg"><span className="you"><Icon name="user" size={16} /></span>
            <div><div className="who">You<span>now</span></div><p>{!place.dm && <span className="mention">@{name}</span>}{!place.dm && " "}{TRY_PROMPTS[prompt]}</p></div></div>
          <div className="try-foot">
            {step === "idle" && <button className="link" onClick={() => setPrompt((prompt + 1) % TRY_PROMPTS.length)}>Try another message</button>}
            {step === "starting" && <>Starting {name}…</>}
            {step === "waiting" && <>Paste and send it in Slack. {ready && <button className="link" onClick={() => void api.open(slackLink(ready, place.id))}>Open Slack again</button>}</>}
            {step === "done" && <span className="replied"><Icon name="check" size={12} />{name} replied in Slack</span>}
            {step === "failed" && <span style={{ color: "var(--red)" }}>{name} didn't start. {error}</span>}
          </div>
        </div>
        <div className="foot">
          {step === "done" ? <><span className="spacer" /><Primary title="Go to Home" onClick={done} /></>
            : step === "idle" || step === "failed" ? <><button className="link" onClick={done}>Later</button><span className="spacer" />
              <Primary title="Start and open Slack" icon="external" onClick={() => void start()} /></>
            : <><button className="link" onClick={done}>Skip to Home</button><span className="spacer" /></>}
        </div>
      </div>
    </>
  );
}

// ---- Anything else Tag asks ------------------------------------------------------------------

function Choose({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [title, body] = heading(question);
  return (
    <FlowBody title={title} lead={body || undefined} foot={<BackIf question={question} back={back} />}>
      <div className="card" role="listbox" aria-label={title}>
        {(question.options ?? []).map((label, index) => label === EXIT_OPTION ? null : (
          <button key={index} className="opt" role="option" aria-selected={question.default === index}
            autoFocus={question.default === index} onClick={() => send(question.option_ids?.[index] ?? index)}>
            <span className="label" style={{ flex: 1, fontWeight: 500 }}>{label}</span><span className="chev"><Icon name="right" size={13} /></span>
          </button>
        ))}
      </div>
    </FlowBody>
  );
}

function Multi({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [title, body] = heading(question);
  const [checked, setChecked] = useState(new Set(question.selected ?? []));
  const flip = (i: number) => setChecked((c) => { const n = new Set(c); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  return (
    <FlowBody title={title} lead={body || undefined}
      foot={<><BackIf question={question} back={back} /><span className="spacer" />
        <Primary title="Continue" disabled={!checked.size && !question.allow_empty} onClick={() => send([...checked].sort((a, b) => a - b))} /></>}>
      <div className="card chan-list">
        {(question.options ?? []).map((label, index) => (
          <button key={index} className="opt" role="checkbox" aria-checked={checked.has(index)} onClick={() => flip(index)}>
            <span className="label" style={{ flex: 1, fontWeight: 500 }}>{label}</span>
            <span className={checked.has(index) ? "box on" : "box"}>{checked.has(index) && <Icon name="check" size={12} />}</span>
          </button>
        ))}
      </div>
    </FlowBody>
  );
}

function Confirm({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  return (
    <FlowBody title={question.prompt}
      foot={<><BackIf question={question} back={back} /><span className="spacer" />
        <Quiet title="No" onClick={() => send(false)} /><Primary title="Yes" onClick={() => send(true)} autoFocus={question.default !== false} /></>} />
  );
}

function TextQuestion({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [title, body] = heading(question);
  const [text, setText] = useState(typeof question.default === "string" ? question.default : "");
  return (
    <FlowBody title={title} lead={body || undefined}
      foot={<><BackIf question={question} back={back} /><span className="spacer" /><Primary title="Continue" disabled={!text} onClick={() => send(text)} /></>}>
      <input className="field" type={question.kind === "secret" ? "password" : "text"} autoFocus value={text}
        aria-label={question.prompt} placeholder={question.kind === "secret" ? "" : question.prompt}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text) send(text); }} />
    </FlowBody>
  );
}
