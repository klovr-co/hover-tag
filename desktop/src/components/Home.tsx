// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Every Tag on this computer, grouped by Slack workspace, under the sky.
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import type { AIStatus } from "../lib/ai";
import type { Bridge } from "../lib/bridge";
import {
  effortLabel, liveRows, modelText, quietLine, roster, rowLine, summary, type ActivityItem, type QuietLine,
} from "../lib/home";
import { groups, problemText, status, title, type Group, type TagRow } from "../lib/protocol";
import type { Tags } from "../lib/tags";
import type { UpdateState } from "../lib/updates";
import { USAGE_DATA_NOTE } from "../lib/telemetry";
import type { SettingsTab } from "./Settings";
import teamArt from "../assets/art/tag-team.png";
import fiveTags from "../assets/art/five-tags.png";
import { useNight } from "../lib/appearance";
import { ordered, useOrder, useReorder } from "../lib/order";
import { useFresh, useListMotion } from "../lib/motion";
import { Avatar, ErrorLine, Icon, MOON, Primary, Sky, Switch, tagIcon, WorkspaceMark } from "./ui";
import { UpdateNotice } from "./UpdateNotice";

export const HOW_TAG_WORKS = "https://hover.team/tag";

/** The folder people open: the Tag's working folder, which holds its private `.tag` data. */
export function workingFolder(row: TagRow & { home?: string }) {
  const home = row.home ?? "";
  return home.endsWith(".tag") ? home.slice(0, -5).replace(/[\\/]$/, "") : `${home}/workspace`;
}

interface Props {
  api: Bridge;
  tags: Tags;
  reports: Record<string, AIStatus>;
  problems: Record<string, string | null>;
  activity: Record<string, ActivityItem[]>;
  firstName: string | null;
  update: UpdateState;
  /** The installed Tag is too old for this app. */
  outdated: boolean;
  runUpdate: () => void;
  add: () => void;
  finishSetup: (row: TagRow) => void;
  open: (row: TagRow) => void;
  fixAI: (row: string) => void;
  /** With `privacy`, Settings opens scrolled to Privacy. */
  showSettings: (tab?: SettingsTab, privacy?: boolean) => void;
  /** Usage data was turned on at first run: say so once. */
  usageNote?: boolean;
  seenUsageNote?: () => void;
  now?: Date;
}

/** The five-Tags art under a short list: stacked above the link (61px tall, plus its gap) when
 *  there's room, a small copy beside the link (32px) when there's less, and none rather than cut off. */
const ART_FULL = 65;
const ART_INLINE = 32;
const GAP = 18;
type ArtSize = "full" | "inline" | "none";

function useArtFits() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<ArtSize>("full");
  useLayoutEffect(() => {
    const el = ref.current;
    // Nothing to measure until the scroll area is laid out (never, in tests).
    if (!el?.clientHeight) return;
    const check = () => {
      const foot = el.querySelector<HTMLElement>(".home-foot");
      if (!foot) return;
      const kids = [...el.children] as HTMLElement[];
      const link = foot.querySelector<HTMLElement>(".link")?.offsetHeight ?? 0;
      const inlineExtra = Math.max(0, ART_INLINE - link);
      const art = foot.classList.contains("full") ? ART_FULL : foot.classList.contains("inline") ? inlineExtra : 0;
      // Measure the children themselves: the scroll area's own height is the window's, not the content's.
      const content = kids.reduce((sum, kid) => sum + kid.offsetHeight, 0) + GAP * Math.max(0, kids.length - 1) - art;
      const room = el.clientHeight - content;
      setSize(room >= ART_FULL ? "full" : room >= inlineExtra ? "inline" : "none");
    };
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    for (const kid of el.children) observer.observe(kid);
    return () => observer.disconnect();
  });
  return { ref, size };
}

export function Home(props: Props) {
  const { api, tags, problems, activity, firstName, update, outdated, add, finishSetup, open, showSettings } = props;
  const rows = tags.rows;
  const setup = rows.filter((row) => status(row) === "setup");
  const live = liveRows(rows);
  const urgent = outdated || update.status === "failed";
  const sum = summary(rows);
  const { shown, extra } = roster(rows);
  const running = live.find((row) => row.state === "running");
  const { order, setWorkspaces, setTags } = useOrder();
  const cards = ordered(groups(live), order);
  const reorder = useReorder(cards.map((g) => g.key), setWorkspaces);
  const art = useArtFits();
  const quiet = rows.length > 2 ? quietLine(rows, problems, activity, props.now ?? new Date(), firstName) : null;
  // A new reply hops its Tag in the header.
  const replyKey = quiet?.kind === "reply" ? `${quiet.tag}@${quiet.when}` : null;
  const replied = useFresh(replyKey) && quiet?.kind === "reply" ? quiet.tag : null;
  return (
    <>
      <Sky kind="home">
        <div className="sky-row">
          {shown.length > 0 && (
            <span className="roster" aria-hidden="true">
              {shown.map((row) => <Avatar key={row.id} row={row} size={36} badge={false} className={row.id === replied ? "hop" : ""} />)}
              {extra > 0 && <span className="more-n">+{extra}</span>}
            </span>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2>Your Tags</h2>
            <div className="sum"><span className={sum.on ? "sq" : "sq off"} />{sum.text}</div>
          </div>
          <button className="sky-btn" aria-label="Settings" title="Settings" onClick={() => showSettings()}><Icon name="gear" size={16} /></button>
          <button className={rows.length > 2 && !setup.length && !urgent ? "add solid" : "add"} onClick={add}>
            <Icon name="plus" />Add Tag
          </button>
        </div>
      </Sky>
      <div className="body home-body">
        <UpdateNotice state={update} outdated={outdated} run={props.runUpdate} settings={() => props.showSettings("updates")} />
        {/* The window stays one height; a long Tag list scrolls here, under the update banner. */}
        <div className="home-scroll" ref={art.ref}>
        {quiet && <Quiet key={replyKey ?? quiet.kind} line={quiet} fresh={!!replied} open={open} rows={rows} fix={props.fixAI} />}
        {tags.loaded && rows.length === 0 && (
          <div className="card empty">
            <img src={teamArt} alt="The Tag characters" />
            <h3>Bring your first Tag to Slack</h3>
            <p>Connect a workspace and Tag sets up its own Slack app.</p>
            <Primary title="Add your first Tag" icon="plus" onClick={add} />
          </div>
        )}
        {setup.length > 0 && (
          <div className="section">
            <div className="sec-head"><h3>Finish setting up</h3><span className="meta">{setup.length === 1 ? "1 Tag" : `${setup.length} Tags`}</span></div>
            <div className="card todo">
              {setup.map((row) => (
                <div key={row.id} className="r">
                  <Avatar row={row} />
                  <div className="txt">
                    <span className="name"><span className="nm">{title(row)}</span></span>
                    <span className="sub"><span className="needs">Not in Slack yet</span>
                      {row.workspace_name && <><span>·</span><span>{row.workspace_name}</span></>}</span>
                  </div>
                  <button className="link" disabled={tags.busy.has(row.id)} onClick={() => open(row)}>Remove…</button>
                  <button className={`p-btn ${urgent ? "soft" : "ink"} sm`} disabled={tags.busy.has(row.id)} onClick={() => finishSetup(row)}>
                    Continue<Icon name="arrow" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
        {cards.map((group) => (
          <WorkspaceCard key={group.key} group={group} drag={reorder.props(group.key)} movable={cards.length > 1}
            setTags={(ids) => setTags(group.key, ids)} {...props} />
        ))}
        {rows.length > 0 && rows.length <= 2 && (
          <>
            {running && (
              <div className="section">
                <div className="sec-head"><h3>Try it in Slack</h3><span className="meta">in a channel you picked</span></div>
                <div className="thread">
                  <div className="smsg">
                    <span className="you"><Icon name="user" size={16} /></span>
                    <div>
                      <div className="who">You<span>now</span></div>
                      <p><span className="mention">@{title(running)}</span> pull this thread into a launch checklist with owners.</p>
                    </div>
                  </div>
                </div>
              </div>
            )}
            <button className="ghost-add" onClick={add}>
              <span className="gi"><Icon name="plus" /></span>
              <span><b>Add another Tag</b><span className="meta">Connect another workspace, or add a second Tag to {live[0]?.workspace_name || "the same one"}.</span></span>
            </button>
            <div className={`home-foot ${art.size}`}>
              {art.size !== "none" && <img className="tags-row" src={fiveTags} alt="" />}
              <button className="link" onClick={() => void api.open(HOW_TAG_WORKS)}>New to Tag? Read how Tag works <Icon name="arrow" /></button>
            </div>
          </>
        )}
        </div>
        {tags.error && <ErrorLine>{tags.error}</ErrorLine>}
        {props.usageNote && <UsageNote seen={props.seenUsageNote} learnMore={() => props.showSettings("general", true)} />}
      </div>
    </>
  );
}

/** The one quiet line between the header and the cards. */
export function Quiet({ line, rows, open, fix, fresh }: { line: QuietLine; rows: TagRow[]; open: (row: TagRow) => void; fix: (tag: string) => void; fresh?: boolean }) {
  const night = useNight();
  if (line.kind === "ai") {
    return (
      <div className="hello warn" role="status">
        <Icon name="warn" />
        <span>{line.cause ? <><b>{line.cause}</b> · {line.who} can't answer</> : <><b>{line.who} can't answer</b> · {line.causes.join(", ")}</>}</span>
        <button className="link" onClick={() => fix(line.tag)}>Fix</button>
      </div>
    );
  }
  if (line.kind === "reply") {
    const row = rows.find((r) => r.id === line.tag)!;
    return (
      <button className={fresh ? "hello fresh" : "hello"} onClick={() => open(row)}>
        <Avatar row={line.avatar} size={20} badge={false} className="" />
        <span><b>{line.name}</b> {line.today ? "just" : "last"} replied in <span className="chan">{line.place}</span></span>
        <span className="when">{line.when}</span>
      </button>
    );
  }
  return (
    <div className="hello">
      <img src={night ? MOON : tagIcon} alt="" />
      <span><b>{line.hello}{line.name ? `, ${line.name}` : ""}.</b> {line.listening ? "Your Tags are listening in Slack." : "Your Tags are taking a break."}</span>
    </div>
  );
}

type Drag = ReturnType<ReturnType<typeof useReorder>["props"]>;

function WorkspaceCard({ group, tags, drag, movable, setTags, ...props }: Props & {
  group: Group; drag: Drag; movable: boolean; setTags: (ids: string[]) => void;
}) {
  const running = group.rows.filter((row) => row.state === "running").length;
  const all = running >= group.rows.length;
  const reorder = useReorder(group.rows.map((row) => row.id), setTags);
  const { ref, onPointerDown, onKeyDown, onClickCapture, ...state } = drag;
  const listed = useListMotion(group.rows, (row) => row.id);
  return (
    <div className={reorder.dragging ? "card sorting" : "card"} ref={ref} {...state}>
      <div className={movable ? "ws-head movable" : "ws-head"} onPointerDown={onPointerDown} onKeyDown={onKeyDown}
        onClickCapture={onClickCapture} tabIndex={movable ? 0 : undefined}
        aria-label={movable ? `${group.label}. Drag, or press Option and an arrow key, to move.` : undefined}>
        {movable && <Grip />}
        <WorkspaceMark label={group.label} icon={group.icon} />
        <h3>{group.label}</h3>
        <span className="meta">{running} of {group.rows.length} online</span>
        <span className="spacer" />
        {group.key && (
          <button className="link" disabled={tags.busy.has(group.key)} aria-label={`${all ? "Stop" : "Start"} all Tags in ${group.label}`}
            onClick={() => void tags.workspace(group.key, all ? "stop" : "start")}>
            <Icon name={all ? "stop" : "play"} size={10} />{all ? "Stop all" : "Start all"}
          </button>
        )}
      </div>
      {listed.map(({ item: row, state: motion }) => motion === "leave"
        ? <div key={row.id} className="r-leave" aria-hidden="true" inert><TagRowView row={row} tags={tags} problem={null} open={() => {}} /></div>
        : <TagRowView key={row.id} row={row} tags={tags} report={props.reports[row.id]} entering={motion === "enter"}
          problem={props.problems[row.id] ?? null} open={() => props.open(row)} drag={reorder.props(row.id)} />)}
    </div>
  );
}

/** One line that, when it's cut off, scrolls to show the rest while its row is hovered. */
function Marquee({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setOverflow(Math.max(0, el.scrollWidth - el.clientWidth));
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, [text]);
  // About 40px a second, so it reads at a calm pace; the row's hover starts it (styles.css).
  const style = overflow ? { "--marquee": `-${overflow}px`, "--marquee-time": `${Math.max(0.6, overflow / 40)}s` } as CSSProperties : undefined;
  return <span ref={ref} className={overflow ? "desc-line marquee" : "desc-line"} title={overflow ? text : undefined} style={style}>
    <span>{text}</span>
  </span>;
}

/** Six dots that appear on hover to say "this can be dragged". */
function Grip() {
  return (
    <svg className="grip" width="6" height="10" viewBox="0 0 6 10" aria-hidden="true">
      {[1, 5, 9].flatMap((y) => [1, 5].map((x) => <circle key={`${x}${y}`} cx={x} cy={y} r="1" />))}
    </svg>
  );
}

const STATE_WORD = { online: "online", offline: "offline", attention: "stopped", setup: "not in Slack yet" };

/** Two lines: the name and its model, then what it's for, unless something needs you. */
export function TagRowView({ row, tags, report, problem, open, drag, entering }: {
  row: TagRow; tags: Tags; report?: AIStatus; problem: string | null; open: () => void; drag?: Drag; entering?: boolean;
}) {
  const state = status(row);
  const on = row.state === "running";
  // Starting shows amber and says so; coming online hops the avatar and ripples its status.
  const starting = tags.busy.has(row.id) && !on;
  const justOn = useFresh(on) && on;
  const motion = `${starting ? " starting" : ""}${justOn ? " just-on" : ""}${entering ? " entering" : ""}`;
  const model = modelText(row, report);
  const line = rowLine(row, problem, problemText(row));
  const backend = report?.default_model.backend_name ?? row.default_model_label?.split(" · ")[0] ?? "";
  const effort = model?.effort ? effortLabel(model.effort) : "";
  const label = [`Open ${title(row)}`, STATE_WORD[state], model?.model, effort && `${effort} thinking`].filter(Boolean).join(", ");
  return (
    <div className={`r click${motion}`} role="button" tabIndex={0} aria-label={label} onClick={open} {...drag}
      onKeyDown={(e) => {
        drag?.onKeyDown(e);
        if (!e.defaultPrevented && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(); }
      }}>
      {drag && <Grip />}
      <Avatar row={row} />
      <div className="txt">
        <span className="name">
          <span className="nm">{title(row)}</span>
          {model && (
            <span className={problem ? "mname bad" : "mname"}
              title={problem ?? `Default model${backend ? ` · ${backend}` : ""}${effort ? ` · ${effort} thinking` : ""}`}>{model.text}</span>
          )}
          {starting && <span className="mname live">Starting…</span>}
          {justOn && <span className="mname now-on">Online</span>}
        </span>
        {line.kind === "error" && <span className="sub bad"><Icon name="warn" /><span>{line.text}</span></span>}
        {line.kind === "ai" && <span className="sub warnline"><Icon name="warn" /><span>{line.text}</span></span>}
        {line.kind === "description" && <Marquee text={line.text} />}
      </div>
      <Switch on={on} busy={tags.busy.has(row.id)} label={`${on ? "Stop" : "Start"} ${title(row)}`} onClick={() => void tags.toggle(row)} />
      <span className="chev" aria-hidden="true"><Icon name="right" size={13} /></span>
    </div>
  );
}

/** The first-run usage data note, at the end of setup or on Home. Learn more opens Settings, where the switch is. */
export function UsageNote({ learnMore, seen }: { learnMore: () => void; seen?: () => void }) {
  useEffect(() => { seen?.(); }, [seen]);
  return (
    <div className="usage-note" role="status">
      <span>{USAGE_DATA_NOTE}</span>
      <button className="link" onClick={learnMore}>Learn more</button>
    </div>
  );
}
