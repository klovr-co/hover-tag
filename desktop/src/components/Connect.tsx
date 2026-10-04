// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Draws the questions Tag's own setup asks over JSON lines; holds no setup logic.
import { useEffect, useReducer, useRef, useState } from "react";
import type { Bridge, Session } from "../lib/bridge";
import { personMatches, type SetupQuestion, type SlackPerson } from "../lib/protocol";
import { EXIT_OPTION, heading, initialSetup, setupReducer, type SetupState, type SignInStep } from "../lib/setup";
import type { RowAction } from "./AI";
import { AgentMark, ConnectionRow, ModelPicker } from "./AI";
import { resultLine } from "../lib/ai";
import { CommunityLinks } from "./CommunityLinks";
import { Back, ErrorLine, Heading, Icon, Primary, Secondary, Spinner, TextButton } from "./ui";
import { SlackCodeModal, SlackComposer, SlackSequence } from "./Slack";

interface Props {
  api: Bridge;
  /** Arguments for `tag … --json`: ["setup"], ["add"], or [tagId, "setup"]. */
  args: string[];
  done: () => void;
}

export function Connect({ api, args, done }: Props) {
  const [state, dispatch] = useReducer(setupReducer, initialSetup);
  const session = useRef<Session | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let live = true;
    void api.setup(args,
      (line) => live && dispatch({ type: "line", line }),
      (code, stderr) => live && dispatch({ type: "exit", code, stderr }),
    ).then((s) => { session.current = s; });
    return () => { live = false; session.current?.stop(); };
  }, [api, args]);

  const send = (answer: unknown) => {
    dispatch({ type: "answered" });
    session.current?.send({ answer });
  };
  const back = () => {
    dispatch({ type: "answered" });
    session.current?.send({ back: true });
  };
  const cancel = () => {
    if (state.signIn.step) session.current?.send({ cancel: true });
    session.current?.send({ answer: null, pause: true });
    done();
  };
  /** Start an agent sign-in from the AI step; the question stays up to show progress. */
  const agentSignIn = (backend: string, answer: string) => {
    dispatch({ type: "agentSignIn", backend });
    session.current?.send({ answer });
  };
  const startTag = async () => {
    setStarting(true);
    await api.tag(state.tag ? [state.tag, "start"] : ["start"]);
    done();
  };

  const q = state.question;
  return (
    <div className="stack gap-14">
      <div className="row" style={{ marginBottom: -6 }}>
        {q?.kind === "slack_login" && <span className="caption secondary" style={{ fontWeight: 500 }}>Step {state.signInStep + 1} of 3</span>}
        <div className="spacer" />
        {!state.outcome && <TextButton title="Cancel" onClick={cancel} />}
      </div>
      {state.outcome === "complete" && (
        <div className="stack gap-14">
          <div className="row gap-8 title3" style={{ color: "var(--green)" }}><Icon name="check" size={18} />Your Tag is ready</div>
          <div className="secondary">Start it, then mention it in one of the channels you picked.</div>
          <div className="divider" />
          <CommunityLinks api={api} />
          <div className="row gap-8"><div className="spacer" />
            <Secondary title="Later" onClick={done} />
            <Primary title={starting ? "Starting…" : "Start Tag"} disabled={starting} onClick={() => void startTag()} autoFocus />
          </div>
        </div>
      )}
      {state.outcome === "paused" && (
        <div className="stack gap-14">
          <Heading title="Progress saved" body="You can finish setting up this Tag from Your Tags at any time." />
          <div className="row"><div className="spacer" /><Primary title="Done" onClick={done} autoFocus /></div>
        </div>
      )}
      {state.outcome === "failed" && (
        <div className="stack gap-14">
          <div className="row gap-8 title3 error"><Icon name="warning" size={18} />Setup stopped</div>
          <div className="secondary selectable">{state.error || "Something went wrong. Your progress is saved."}</div>
          <div className="row"><div className="spacer" /><Primary title="Done" onClick={done} autoFocus /></div>
        </div>
      )}
      {!state.outcome && q && (
        q.kind === "slack_login" ? (
          <SignIn api={api} question={q} step={state.signInStep}
            setStep={(step) => dispatch({ type: "signIn", step })} send={send} />
        ) : q.id === "ai_connection" && q.connections ? (
          <AIStep api={api} question={q} signIn={state.signIn} send={send} back={back} startSignIn={agentSignIn}
            cancelSignIn={() => session.current?.send({ cancel: true })} />
        ) : q.id === "default_model" && q.groups ? (
          <ModelStep key={JSON.stringify(q.option_ids)} question={q} signIn={state.signIn} send={send} back={back}
            startSignIn={agentSignIn} cancelSignIn={() => session.current?.send({ cancel: true })} />
        ) : q.kind === "choose" ? <Choose key={JSON.stringify(q)} question={q} send={send} back={back} />
          : q.kind === "multi" ? <Multi key={JSON.stringify(q)} question={q} send={send} back={back} />
          : q.kind === "people" ? <People key={JSON.stringify(q)} question={q} send={send} />
          : q.kind === "confirm" ? <Confirm key={JSON.stringify(q)} question={q} send={send} back={back} />
          : <TextQuestion key={JSON.stringify(q)} question={q} send={send} back={back} />
      )}
      {!state.outcome && !q && (
        <div className="row gap-10 secondary" style={{ justifyContent: "center", padding: "30px 0" }}>
          <Spinner />{state.status}
        </div>
      )}
      {state.error && !state.outcome && <ErrorLine>{state.error}</ErrorLine>}
    </div>
  );
}

/** Setup's AI step: every agent's connection, and one usable one to continue. */
/** Setup lists connections only while nothing usable is connected; Settings manages them otherwise. */
function AIStep({ api, question, signIn, send, back, startSignIn, cancelSignIn }: {
  api: Bridge; question: SetupQuestion; signIn: SetupState["signIn"];
  send: (a: unknown) => void; back: () => void; startSignIn: (backend: string, answer: string) => void; cancelSignIn: () => void;
}) {
  const [title, body] = heading(question);
  const [opened, setOpened] = useState(new Set<string>());
  const ids = question.option_ids ?? [];
  const signingIn = !!signIn.step;
  const act = (action: RowAction) => {
    if (action.kind === "install" || action.kind === "update") {
      void api.open(action.url);
      setOpened((current) => new Set(current).add(action.backend));
    } else {
      const id = `${action.kind}:${action.backend}`;
      if (ids.includes(id)) startSignIn(action.backend, id);
    }
  };
  return (
    <div className="stack gap-14">
      <Heading title={title} body={body} />
      <div className="card">
        {(question.connections ?? []).filter((c) => c.allowed !== false).map((connection, index) => (
          <div key={connection.backend}>
            {index > 0 && <div className="divider" style={{ marginLeft: 58 }} />}
            <ConnectionRow connection={{ ...connection, actions: connection.actions.filter((a) => a !== "change_account") }}
              busy={signingIn} signIn={signIn.backend === connection.backend ? signIn : null}
              opened={opened.has(connection.backend)} act={act} cancel={cancelSignIn}
              check={() => send("check")} open={(url) => void api.open(url)} />
          </div>
        ))}
      </div>
      <div className="row gap-8">
        {!signingIn && <BackIf question={question} back={back} />}
        <div className="spacer" />
        <Secondary title="Check again" icon="refresh" disabled={signingIn} onClick={() => send("check")} />
      </div>
    </div>
  );
}

const SIGN_IN_WORD: Record<string, string> = { sign_in: "Sign in", reconnect: "Reconnect", resume: "Resume" };

/** The Tag's default model, from every connected account; the model picks the agent. */
function ModelStep({ question, signIn, send, back, startSignIn, cancelSignIn }: {
  question: SetupQuestion; signIn: SetupState["signIn"]; send: (a: unknown) => void; back: () => void;
  startSignIn: (backend: string, answer: string) => void; cancelSignIn: () => void;
}) {
  const [title, body] = heading(question);
  const ids = question.option_ids ?? [];
  const offered = new Set((question.groups ?? []).flatMap((g) => g.models.map((m) => m.value)));
  const [value, setValue] = useState<string | null>(() => {
    const preset = typeof question.default === "number" ? ids[question.default] : null;
    return preset && offered.has(preset) ? preset : null;
  });
  const signingIn = !!signIn.step;
  // Anything that isn't a model is another agent to sign in to, such as "sign_in:claude".
  const others = ids.filter((id) => !offered.has(id)).map((id) => {
    const [action, backend] = id.split(":");
    return { id, action, backend, name: question.connections?.find((c) => c.backend === backend)?.name ?? backend };
  });
  return (
    <div className="stack gap-14">
      <Heading title={title} body={body} />
      <ModelPicker groups={question.groups ?? []} value={value} onChange={setValue} disabled={signingIn} />
      {others.map((other) => {
        const mine = signIn.backend === other.backend;
        return (
          <div key={other.id} className="row gap-8 callout secondary" style={{ padding: "0 2px" }}>
            <AgentMark backend={other.backend} size={20} />
            {mine && signIn.step ? (
              <><Spinner small /><span style={{ color: "var(--accent)" }}>{signIn.text}</span><div className="spacer" />
                <button className="link-btn" onClick={cancelSignIn}>Cancel</button></>
            ) : (
              <><span>{mine && signIn.result && signIn.result.status !== "connected"
                ? resultLine(signIn.result, other.name) : `Use ${other.name} models too?`}</span><div className="spacer" />
                <button className="link-btn" disabled={signingIn} onClick={() => startSignIn(other.backend, other.id)}>
                  {mine && signIn.result?.status === "failed" ? "Try again" : SIGN_IN_WORD[other.action] ?? "Sign in"}
                </button></>
            )}
          </div>
        );
      })}
      <div className="row">
        {!signingIn && <BackIf question={question} back={back} />}
        <div className="spacer" />
        <Primary title="Continue" disabled={!value || signingIn} onClick={() => send(value)} autoFocus />
      </div>
    </div>
  );
}

const BackIf = ({ question, back }: { question: SetupQuestion; back: () => void }) =>
  question.can_go_back ? <Back onClick={back} /> : null;

function Step({ art, title, body, children }: { art: React.ReactNode; title: string; body: string; children?: React.ReactNode }) {
  return (
    <div className="stack gap-14">
      <div className="illustration well">{art}</div>
      <Heading title={title} body={body} />
      {children && <div className="row gap-8" style={{ justifyContent: "flex-end" }}>{children}</div>}
    </div>
  );
}

function SignIn({ api, question, step, setStep, send }: {
  api: Bridge; question: SetupQuestion; step: SignInStep; setStep: (s: SignInStep) => void; send: (a: unknown) => void;
}) {
  const [code, setCode] = useState("");
  return (
    <>
      <div className="segments" aria-hidden="true">{[0, 1, 2].map((n) => <div key={n} className={n <= step ? "on" : ""} />)}</div>
      {step === 0 && (
        <Step art={<SlackComposer pasted={false} />} title="Copy your sign-in line"
          body="Tag creates a one-time line that tells Slack this computer is yours.">
          <Primary title="Copy sign-in line" icon="copy" autoFocus
            onClick={() => { void api.copy(question.sign_in_line ?? ""); setStep(1); }} />
        </Step>
      )}
      {step === 1 && (
        <Step art={<SlackSequence />} title="Paste it in Slack"
          body="Open the workspace for this Tag, paste into any message box and send. Then click Confirm.">
          <Back onClick={() => setStep(0)} />
          <div className="spacer" />
          <Secondary title="Open Slack" icon="external" onClick={() => void api.open("slack://open")} />
          <Primary title="I clicked Confirm" onClick={() => setStep(2)} autoFocus />
        </Step>
      )}
      {step === 2 && (
        <>
          <Step art={<SlackCodeModal />} title="Paste the code" body="Slack now shows a short code. Copy it and paste it here." />
          <div className="row gap-8">
            <input className="field mono" placeholder="Code from Slack" value={code} autoFocus aria-label="Code from Slack"
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && code.trim()) send(code.trim()); }} />
            <Secondary title="Paste" icon="paste" onClick={() => void api.paste().then((t) => setCode(t.trim()))} />
          </div>
          <div className="row gap-8">
            <Back onClick={() => setStep(1)} />
            <div className="spacer" />
            <TextButton title="Copy the line again" onClick={() => setStep(0)} />
            <Primary title="Connect" disabled={!code.trim()} onClick={() => send(code.trim())} />
          </div>
        </>
      )}
    </>
  );
}

function Choose({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [title, body] = heading(question);
  return (
    <div className="stack gap-14">
      <Heading title={title} body={body} />
      <div className="card" role="listbox" aria-label={title}>
        {(question.options ?? []).map((label, index) => label === EXIT_OPTION ? null : (
          <button key={index} className="list-item" role="option" aria-selected={question.default === index}
            autoFocus={question.default === index} onClick={() => send(index)}>
            <span>{label}</span><div className="spacer" /><span className="tertiary"><Icon name="right" size={11} /></span>
          </button>
        ))}
      </div>
      <div className="row"><BackIf question={question} back={back} /></div>
    </div>
  );
}

function Multi({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [title, body] = heading(question);
  const [checked, setChecked] = useState(new Set(question.selected ?? []));
  const flip = (i: number) => setChecked((c) => { const n = new Set(c); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  return (
    <div className="stack gap-14">
      <Heading title={title} body={body} />
      <div className="card" style={{ maxHeight: 300, overflow: "auto" }}>
        {(question.options ?? []).map((label, index) => (
          <label key={index} className="list-item" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={checked.has(index)} onChange={() => flip(index)} style={{ accentColor: "var(--accent)" }} />
            <span>{label}</span>
          </label>
        ))}
      </div>
      <div className="row">
        <BackIf question={question} back={back} />
        <div className="spacer" />
        <Primary title="Continue" disabled={!checked.size} onClick={() => send([...checked].sort((a, b) => a - b))} />
      </div>
    </div>
  );
}

function Confirm({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  return (
    <div className="stack gap-14">
      <Heading title={question.prompt} />
      <div className="row gap-8">
        <BackIf question={question} back={back} />
        <div className="spacer" />
        <Secondary title="No" onClick={() => send(false)} />
        <Primary title="Yes" onClick={() => send(true)} autoFocus={question.default !== false} />
      </div>
    </div>
  );
}

function TextQuestion({ question, send, back }: { question: SetupQuestion; send: (a: unknown) => void; back: () => void }) {
  const [title, body] = heading(question);
  const [text, setText] = useState(typeof question.default === "string" ? question.default : "");
  return (
    <div className="stack gap-14">
      <Heading title={title} body={body} />
      <input className="field" type={question.kind === "secret" ? "password" : "text"} autoFocus value={text}
        aria-label={question.prompt} placeholder={question.kind === "secret" ? "" : question.prompt}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && text) send(text); }} />
      <div className="row">
        <BackIf question={question} back={back} />
        <div className="spacer" />
        <Primary title="Continue" disabled={!text} onClick={() => send(text)} />
      </div>
    </div>
  );
}

/** Search stays local; choosing someone sends only their Slack member ID. */
function People({ question, send }: { question: SetupQuestion; send: (a: unknown) => void }) {
  const [search, setSearch] = useState("");
  const matches = (question.people ?? []).filter((p) => personMatches(p, search));
  return (
    <div className="stack gap-14">
      <Heading title="Which one is you?"
        body="You'll own this Tag. Only you can use it until you turn on access control in settings." />
      <input className="field" placeholder="Search by name or username" value={search} autoFocus
        aria-label="Search people" onChange={(e) => setSearch(e.target.value)} />
      <div className="card" style={{ height: 240, overflow: "auto" }}>
        {!matches.length && <div className="secondary" style={{ padding: 20 }}>No people found. Try another name or enter a member ID.</div>}
        {matches.map((person) => <PersonRow key={person.id} person={person} onClick={() => send(person.id)} />)}
      </div>
      <div><TextButton title="Enter a member ID instead" onClick={() => send("manual")} /></div>
    </div>
  );
}

function PersonRow({ person, onClick }: { person: SlackPerson; onClick: () => void }) {
  const [failed, setFailed] = useState(!person.image_url);
  return (
    <button className="list-item" onClick={onClick} style={{ padding: 12 }}>
      {failed ? (
        <span style={{ width: 36, height: 36, borderRadius: 8, background: "var(--accent-soft)", display: "grid", placeItems: "center", fontWeight: 600 }}>
          {person.name.slice(0, 1).toUpperCase()}
        </span>
      ) : (
        <img src={person.image_url ?? ""} alt="" width={36} height={36} style={{ borderRadius: 8, objectFit: "cover" }} onError={() => setFailed(true)} />
      )}
      <span className="stack gap-4">
        <span style={{ fontWeight: 500 }}>{person.name}</span>
        {person.username && <span className="caption secondary">@{person.username}</span>}
      </span>
      <div className="spacer" /><span className="tertiary"><Icon name="right" size={11} /></span>
    </button>
  );
}
