// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Sample threads for `npm run dev` and the tests: what `tag NAME threads` and `thread` report.
import threadExample from "../../../protocol/examples/thread.json";
import type { Prompt, ThreadDetail, ThreadItem, ThreadRow } from "./threads";

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

/** The example thread, moved to a few minutes ago so it reads as live. */
function launchThread(tag: string): ThreadDetail {
  const thread = structuredClone(threadExample.thread) as unknown as ThreadDetail;
  const minutes = [9, 8, 8, 4, 3];
  thread.items.forEach((item, index) => {
    if ("at" in item) item.at = ago(minutes[index] ?? 3);
    if (item.type === "work") { item.started_at = ago(9); item.finished_at = ago(8.5); }
    if (item.type === "answer") {
      item.files.forEach((file) => { file.at = ago(8); });
      item.memory.forEach((change) => { change.at = ago(8); });
      item.text = [
        "**The launch checklist is ready.** I split it by owner and flagged what still needs a date.", "",
        "## Before launch", "",
        "| Item | Owner | Due |", "| --- | --- | --- |", "| Press kit | Maya | Mon 2 Nov |", "| Pricing page | Sam | Wed 4 Nov |", "| Support macros | Priya | _not set_ |", "",
        "- [x] Collected the notes from #launch", "- [x] Wrote `launch-checklist.md`", "- [ ] Confirm the support date with Priya", "",
        "I also saved who owns launch comms, so I'll ask Maya about announcements next time.",
      ].join("\n");
    }
  });
  thread.files = thread.items.flatMap((item) => item.type === "answer" ? item.files : []);
  thread.memory = thread.items.flatMap((item) => item.type === "answer" ? item.memory : []);
  thread.prompts = thread.items.filter((item): item is Prompt => item.type === "prompt");
  thread.started_at = ago(9);
  thread.updated_at = ago(3);
  return { ...thread, link: thread.link.replace(threadExample.tag, tag) };
}

function thread(tag: string, row: Omit<ThreadRow, "link" | "team" | "source" | "preview_only" | "pending_prompts" | "message_count" | "file_count" | "memory_count" | "last_answer_preview"> & Partial<ThreadRow>, items: ThreadItem[]): ThreadDetail {
  const answers = items.filter((item) => item.type === "answer");
  const files = answers.flatMap((item) => item.type === "answer" ? item.files : []);
  const memory = answers.flatMap((item) => item.type === "answer" ? item.memory : []);
  const prompts = items.filter((item): item is Prompt => item.type === "prompt");
  const last = answers[answers.length - 1];
  return {
    team: "T0KLOVR1", source: "slack", preview_only: false, message_count: items.filter((item) => item.type === "message" || item.type === "answer").length,
    file_count: files.length, memory_count: memory.length, pending_prompts: prompts.filter((p) => p.status === "pending").length,
    last_answer_preview: last && last.type === "answer" ? last.text.split("\n")[0].replace(/\*\*/g, "").slice(0, 140) : null,
    link: `hover-tag://tag/${tag}/thread/${row.channel}/${row.thread_ts}`,
    ...row, items, files, memory, prompts,
  };
}

const maya = { id: "U0MAYA001", name: "Maya" };
const work = (run: string, minutes: number, steps: string[], status = "completed"): ThreadItem => ({
  type: "work", run_id: run.repeat(32).slice(0, 32), status, started_at: ago(minutes), finished_at: status === "running" ? null : ago(minutes - 1),
  steps: steps.map((label, index) => ({ id: `${run}${index}`, label, status: status === "running" && index === steps.length - 1 ? "running" : "completed" })),
  step_count: steps.length, duration_seconds: status === "running" ? undefined : 52, via: "slack", requester: maya,
});
const answer = (run: string, minutes: number, text: string, extra: Partial<Extract<ThreadItem, { type: "answer" }>> = {}): ThreadItem => ({
  type: "answer", at: ago(minutes), run_id: run.repeat(32).slice(0, 32), text, outcome: "completed", posted_to: "slack", files: [], memory: [], ...extra,
});
const said = (minutes: number, text: string, author = maya, via = "slack"): ThreadItem => ({ type: "message", at: ago(minutes), ts: null, author, text, via });

function samples(tag: string, first: boolean, second: boolean): ThreadDetail[] {
  if (first) return [
    launchThread(tag),
    thread(tag, { channel: "D0MAYA001", thread_ts: "1792401200.000100", title: "What did we decide about the beta invite list?", status: "done",
      started_at: ago(130), updated_at: ago(125), people: ["Maya"], channel_name: null, dm: true }, [
      said(130, "@Tag what did we decide about the beta invite list?"),
      work("d", 129, ["Searching Slack…", "Searching memory…"]),
      answer("d", 125, "You decided to invite the **first 200 people** on the waitlist, starting Monday.\n\nSam asked to keep 20 places for design partners; nobody objected. [The decision](https://example.slack.com/archives/C0LAUNCH1/p1792400000000100) is in #launch."),
    ]),
    thread(tag, { channel: "C0LAUNCH1", thread_ts: "1792100000.000100", title: "Summarise this week's launch decisions", status: "done",
      started_at: ago(3 * 1440), updated_at: ago(3 * 1440 - 2), people: ["Maya"], channel_name: "launch", dm: false }, [
      said(3 * 1440, "@Tag summarise this week's launch decisions"),
      work("e", 3 * 1440 - 1, ["Searching Slack…"]),
      answer("e", 3 * 1440 - 2, "Three decisions this week:\n\n1. Launch moves to **Tuesday 10 Nov**.\n2. Pricing stays at the beta price for 3 months.\n3. Support covers weekends in launch week."),
    ]),
    { ...thread(tag, { channel: "C0LAUNCH1", thread_ts: "1791800000.000100", title: "Drafted the release notes for v0.3.", status: "done",
      started_at: ago(6 * 1440), updated_at: ago(6 * 1440), people: [], channel_name: "launch", dm: false }, [
      { ...work("f", 6 * 1440, []), steps: [], step_count: 6, preview_only: true } as ThreadItem,
      answer("f", 6 * 1440, "Drafted the release notes for v0.3.", { preview_only: true }),
    ]), preview_only: true },
  ];
  if (second) return [
    thread(tag, { channel: "C0LAUNCHOPS", thread_ts: "1792488500.000100", title: "Draft the on-call handover for launch week", status: "working",
      started_at: ago(2), updated_at: ago(0.2), people: ["Maya"], channel_name: "launch-ops", dm: false, team: "T0ACME01" }, [
      said(2, "Draft the on-call handover for launch week. Keep it short.", maya, "app"),
      work("g", 1.8, ["Reading a file…", "Searching memory…", "Writing files…"], "running"),
    ]),
    thread(tag, { channel: "C0GENERAL2", thread_ts: "1792300000.000100", title: "Reconcile the October invoices", status: "failed",
      started_at: ago(26 * 60), updated_at: ago(26 * 60 - 3), people: ["Maya"], channel_name: "general", dm: false, team: "T0ACME01" }, [
      said(26 * 60, "@Ops Tag reconcile the October invoices"),
      work("h", 26 * 60 - 1, ["Using a connected tool…"], "failed"),
      answer("h", 26 * 60 - 3, "I couldn't finish: the accounting connection timed out twice. Try again in a few minutes.", { outcome: "failed" }),
    ]),
  ];
  return [];
}

const summary = ({ items: _items, files: _files, memory: _memory, prompts: _prompts, ...row }: ThreadDetail): ThreadRow => row;

/** `tag ID threads …` and `tag ID thread …` against sample data that replies and answers change. */
export function demoThreads() {
  const byTag = new Map<string, ThreadDetail[]>();
  const listFor = (tag: string, first: boolean, second: boolean) => {
    if (!byTag.has(tag)) byTag.set(tag, samples(tag, first, second));
    return byTag.get(tag)!;
  };
  const fail = (error: string, code: string) => ({ ok: false as const, error, code });
  const settle = (thread: ThreadDetail, run: string, text: string) => setTimeout(() => {
    const working = thread.items.find((item) => item.type === "work" && item.run_id === run);
    if (working?.type === "work") { working.status = "completed"; working.finished_at = new Date().toISOString(); working.steps.forEach((s) => { s.status = "completed"; }); }
    thread.items.push(answer("z", 0, text, { run_id: run, posted_to: "app" }));
    thread.status = thread.prompts.some((p) => p.status === "pending") ? "needs_you" : "done";
    thread.updated_at = new Date().toISOString();
    thread.last_answer_preview = text;
  }, 2500);
  return (tag: string, first: boolean, second: boolean, running: boolean, args: string[]) => {
    const threads = listFor(tag, first, second);
    if (args[0] === "threads") {
      return { ok: true, tag, retention_days: 90, has_more: false,
        threads: threads.map(summary).sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? "")) };
    }
    const [, channel, ts, action, ...rest] = args;
    const thread = threads.find((t) => t.channel === channel && t.thread_ts === ts);
    if (!thread) return fail("This thread isn't on this computer. Open the link on the computer that runs the Tag.", "not_found");
    if (!action || action === "--json") return { ok: true, tag, thread };
    if (!running) return fail("Start the Tag first. It replies and answers only while it runs.", "not_running");
    const run = Math.random().toString(16).slice(2).padEnd(32, "0").slice(0, 32);
    if (action === "reply") {
      const text = (rest.find((arg) => arg.startsWith("--text=")) ?? "").slice(7).trim();
      if (!text) return fail("Write a reply first.", "invalid");
      const post = rest.includes("--post-to-slack");
      thread.items.push({ type: "message", at: new Date().toISOString(), ts: null, author: maya, text, via: "app", posted_to_slack: post },
        { type: "work", run_id: run, status: "running", started_at: new Date().toISOString(), finished_at: null,
          steps: [{ id: "r0", label: "Thinking…", status: "running" }], step_count: 1, via: "app", requester: maya });
      thread.status = "working";
      thread.updated_at = new Date().toISOString();
      settle(thread, run, `Done. I added that to the thread${post ? " and posted it in Slack" : ""}.`);
      return { ok: true, tag, request_id: run, queued: true, post_to_slack: post };
    }
    const [prompt, choice] = rest;
    const waiting = thread.prompts.find((p) => p.prompt_id === prompt && p.status === "pending");
    if (!waiting) return fail("This question isn't waiting for an answer any more.", "invalid");
    const picked = waiting.choices.find((c) => c.id === choice);
    if (!picked) return fail(`Choose one of: ${waiting.choices.map((c) => c.id).join(", ")}.`, "invalid");
    waiting.status = "answered";
    waiting.answer = { choice: picked.id, label: picked.label, by: maya, via: "app", at: new Date().toISOString() };
    thread.pending_prompts = thread.prompts.filter((p) => p.status === "pending").length;
    if (choice === "ignore") { thread.status = "done"; return { ok: true, tag, request_id: run, queued: true }; }
    thread.items.push({ type: "work", run_id: run, status: "running", started_at: new Date().toISOString(), finished_at: null,
      steps: [{ id: "a0", label: "Writing files…", status: "running" }], step_count: 1, via: "app", requester: maya });
    thread.status = "working";
    thread.updated_at = new Date().toISOString();
    settle(thread, run, "Added the pricing FAQ to `launch-checklist.md`.");
    return { ok: true, tag, request_id: run, queued: true };
  };
}

