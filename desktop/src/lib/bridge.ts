// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Everything the UI asks of the operating system goes through here. In Tag.app
// it calls the Rust side (src-tauri); in a plain browser, or with
// TAG_INSTALLER_DEMO set, it plays sample data so nothing real changes.
import productVersion from "../../../VERSION?raw";
import listExample from "../../../protocol/examples/list.json";
import type { TagRow } from "./protocol";
import versionExample from "../../../protocol/examples/version.json";
import progressExample from "../../../protocol/examples/install-progress.txt?raw";
import setupExample from "../../../protocol/examples/setup.jsonl?raw";
import startExample from "../../../protocol/examples/start-progress.jsonl?raw";
import aiStatusExample from "../../../protocol/examples/ai-status.json";
import aiModelsExample from "../../../protocol/examples/ai-models.json";
import type { AIStatus, Connection } from "./ai";
import type { ActivityItem } from "./home";
import { windowFitter } from "./fit";

export interface AppInfo {
  platform: "macos" | "windows" | "linux" | string;
  demo: boolean;
  /** Path of the `tag` command, when Tag is installed. */
  cli: string | null;
  version: string;
  /** Version 1 migration: this installation has adopted its app release channel. */
  channelInitialized?: boolean;
  launchedAtLogin: boolean;
  /** Tags the earlier Mac app kept running at login, to carry over once. */
  legacyWantedTags: string[] | null;
  /** The person's first name from their computer account, for Home's greeting. */
  firstName?: string | null;
  /** A development build, such as `./tag app`: it never offers or installs updates. */
  development?: boolean;
}

export interface RunResult {
  code: number;
  stdout: string;
  stderr: string;
}

export interface Session {
  send(message: unknown): void;
  stop(): void;
}

export interface TrayTag {
  id: string;
  title: string;
  group: string;
  running: boolean;
  needsSetup: boolean;
}

export interface Bridge {
  info(): Promise<AppInfo>;
  tag(args: string[]): Promise<RunResult>;
  /** `tag ARGS --json` setup conversation: one stdout line per callback. */
  setup(args: string[], onLine: (line: string) => void, onExit: (code: number, stderr: string) => void): Promise<Session>;
  /** `tag ARGS --json` for a command that reports progress as it goes, such as start. */
  follow(args: string[], onLine: (line: string) => void, onExit: (code: number, stderr: string) => void): Promise<Session>;
  install(channel: string, onLine: (line: string) => void, onExit: (code: number) => void): Promise<Session>;
  copy(text: string): Promise<void>;
  paste(): Promise<string>;
  open(target: string): Promise<void>;
  updateTray(tags: TrayTag[], keepRunning: boolean): Promise<void>;
  onTray(handler: (action: string, tag?: string) => void): () => void;
  openAtLogin(enabled?: boolean): Promise<boolean>;
  notify(title: string, body: string): Promise<void>;
  showWindow(): Promise<void>;
  /** Size the window to its content; Tag detail is wider than the other screens. */
  fitWindow(width: number, height: number): Promise<void>;
  quit(): Promise<void>;
  /** Record that the Swift app's login behaviour was carried over. */
  markMigrated(): Promise<void>;
  markChannelInitialized(): Promise<void>;
  /** A newer signed Tag.app on `channel` (stable, beta, or alpha); otherwise on this build's own release line. */
  checkAppUpdate(channel?: string): Promise<AppUpdate | null>;
  /** Install the update found by checkAppUpdate and restart into it. */
  installAppUpdate(version: string): Promise<void>;
  /** Choose a picture file; Tag checks it. Null when the person cancels. */
  pickImage(): Promise<string | null>;
  /** Files dropped on the window, while `over` reports a drag above it. */
  onFileDrop(handler: (paths: string[]) => void, over?: (dragging: boolean) => void): () => void;
}

export interface AppUpdate {
  version: string;
  notes?: string | null;
}

const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

// ---- Tag.app -----------------------------------------------------------------

async function tauriBridge(): Promise<Bridge> {
  const { invoke, Channel } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");
  const clipboard = await import("@tauri-apps/plugin-clipboard-manager");
  const opener = await import("@tauri-apps/plugin-opener");
  const autostart = await import("@tauri-apps/plugin-autostart");
  const notification = await import("@tauri-apps/plugin-notification");
  const { getCurrentWindow, LogicalSize } = await import("@tauri-apps/api/window");

  type Output = { event: "line"; data: string } | { event: "exit"; data: { code: number; stderr: string } };
  const stream = async (command: string, args: Record<string, unknown>,
    onLine: (line: string) => void, onExit: (code: number, stderr: string) => void): Promise<Session> => {
    const channel = new Channel<Output>();
    channel.onmessage = (message) => {
      if (message.event === "line") onLine(message.data);
      else onExit(message.data.code, message.data.stderr);
    };
    const id = await invoke<number>(command, { ...args, output: channel });
    return {
      send: (message) => void invoke("session_send", { id, line: JSON.stringify(message) }),
      stop: () => void invoke("session_stop", { id }),
    };
  };

  return {
    info: () => invoke<AppInfo>("app_info"),
    tag: (args) => invoke<RunResult>("run_tag", { args }),
    setup: (args, onLine, onExit) => stream("setup_start", { args }, onLine, onExit),
    // The same streamed `tag ARGS --json` session as setup; start never reads input.
    follow: (args, onLine, onExit) => stream("setup_start", { args }, onLine, onExit),
    install: (channel, onLine, onExit) => stream("install_start", { channel }, onLine, (code) => onExit(code)),
    copy: (text) => clipboard.writeText(text),
    paste: async () => (await clipboard.readText()) ?? "",
    open: (target) => (/^[a-z]+:/i.test(target) ? opener.openUrl(target) : opener.openPath(target)),
    updateTray: (tags, keepRunning) => invoke("update_tray", { tags, keepRunning }),
    onTray: (handler) => {
      const unlisten = listen<{ action: string; tag?: string }>("tray", (e) => handler(e.payload.action, e.payload.tag));
      return () => void unlisten.then((stop) => stop());
    },
    openAtLogin: async (enabled) => {
      if (enabled === true) await autostart.enable();
      if (enabled === false) await autostart.disable();
      return autostart.isEnabled();
    },
    notify: async (title, body) => {
      let granted = await notification.isPermissionGranted();
      if (!granted) granted = (await notification.requestPermission()) === "granted";
      if (granted) notification.sendNotification({ title, body });
    },
    showWindow: () => invoke("show_window"),
    fitWindow: windowFitter((width, height) => getCurrentWindow().setSize(new LogicalSize(width, height)), window),
    quit: () => invoke("quit"),
    markMigrated: () => invoke("mark_migrated"),
    markChannelInitialized: () => invoke("mark_channel_initialized"),
    checkAppUpdate: (channel) => invoke<AppUpdate | null>("app_update_check", { channel: channel ?? null }),
    installAppUpdate: (version) => invoke("app_update_install", { version }),
    pickImage: async () => {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const picked = await open({ multiple: false, directory: false, filters: [{ name: "Pictures", extensions: ["png", "jpg", "jpeg", "gif"] }] });
      return typeof picked === "string" ? picked : null;
    },
    onFileDrop: (handler, over) => {
      const stop = import("@tauri-apps/api/webview").then(({ getCurrentWebview }) => getCurrentWebview().onDragDropEvent((event) => {
        if (event.payload.type === "drop") { over?.(false); handler(event.payload.paths); }
        else if (event.payload.type === "leave") over?.(false);
        else over?.(true);
      }));
      return () => void stop.then((unlisten) => unlisten());
    },
  };
}

// ---- Sample data -------------------------------------------------------------

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function demoBridge(options: { installed?: boolean } = {}): Bridge {
  const sharedAI = structuredClone(aiStatusExample) as AIStatus;
  const aiDemo = new Map<string, AIStatus>();
  const aiFor = (tag: string, row?: TagRow) => demoAIFor(aiDemo, sharedAI, tag, row);
  const rows: (TagRow & { home: string })[] = structuredClone(listExample.tags);
  rows.splice(1, 0, {
    ...rows[0], id: "t0klovr1-a0rese02", slack_name: "Research Tag", state: "stopped",
    nickname: "research", main: false, keep_running: false,
    description: "Digs through docs and old threads to answer research questions.",
    default_model: "claude:claude-opus-5-5", default_model_label: "Claude · Opus 5.5", default_model_name: "Opus 5.5", default_effort: "max",
    channels: [{ id: "C0RESEARCH", name: "research" }],
  });
  rows.splice(2, 0, {
    ...rows[0], id: "t0acme01-a0ops003", slack_name: "Ops Tag", slack_workspace: "T0ACME01",
    workspace_name: "Acme Inc", workspace_icon: null, main: false,
    description: "Keeps on-call notes and launch checklists up to date.",
    default_model: "codex:gpt-5.5-mini", default_model_label: "Codex · GPT-5.5 mini", default_model_name: "GPT-5.5 mini", default_effort: "medium",
    channels: [{ id: "C0GENERAL2", name: "general" }, { id: "C0LAUNCHOPS", name: "launch-ops" }],
  });
  // Sample data shows only what Tag itself would report: replies come from its activity records.
  const today = (hours: number, minutes: number) => { const d = new Date(); d.setHours(hours, minutes, 0, 0); return d.toISOString(); };
  const activity: Record<string, ActivityItem[]> = {
    [rows[0].id]: [{ run_id: "a".repeat(32), reply_preview: "I reviewed the launch notes and prepared the following checklist.", reply_summary: "Created a launch checklist with owners and next steps.", artifacts: [{ name: "launch-checklist.md", kind: "file", delivery: "uploaded", url: "https://example.slack.com/files/F123/launch-checklist.md" }], model: "gpt-5.5", model_name: "GPT-5.5", reasoning_effort: "medium", backend: "codex", duration_seconds: 43, step_count: 7, usage: {input_tokens: 11900, output_tokens: 440, total_tokens: 12340, cache_read_input_tokens: 9000, reasoning_output_tokens: 180}, at: today(9, 20), kind: "replied", channel: "C0LAUNCH1", channel_name: "launch", dm: false }],
    "t0acme01-a0ops003": [{ run_id: "b".repeat(32), at: today(10, 12), kind: "replied", channel: "C0LAUNCHOPS", channel_name: "launch-ops", dm: false }],
  };
  // ?tags=N shows only the first N sample Tags, for checking Home with one or two.
  const shown = typeof location === "undefined" ? null : new URLSearchParams(location.search).get("tags");
  if (shown !== null) rows.splice(Number(shown));
  let installed = options.installed ?? true;
  let runtimeVersion = productVersion.trim();
  let desktopVersion = runtimeVersion;
  let savedChannel = runtimeVersion.includes("-alpha") ? "alpha" : runtimeVersion.includes("-beta") ? "beta" : "stable";
  const targetVersion = new URLSearchParams(location.search).get("update") ? "0.4.0-alpha.1" : runtimeVersion;
  let keepRunning = false;
  let loginItem = false;
  // ?telemetry=ask shows the first-run usage data notice.
  let telemetry = new URLSearchParams(location.search).get("telemetry") === "ask" ? "not_set" : "on";
  const json = (value: unknown): RunResult => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
  return {
    info: async () => ({
      platform: "macos", demo: true, cli: installed ? "~/.local/bin/tag" : null,
      version: desktopVersion, launchedAtLogin: false, legacyWantedTags: null, firstName: "Maya",
      channelInitialized: true,
    }),
    tag: async (args) => {
      await sleep(250);
      const [first, second] = args;
      if (first === "settings" && second === "ai" && args[2] === "connections") {
        return json({ connections: sharedAI.connections, usable: sharedAI.connections.filter((c) => c.state === "connected").map((c) => c.backend),
          running: rows.some((r) => r.state === "running"), scope: "installation" });
      }
      if (first === "list") return json({ schema_version: 1, tags: rows });
      if (first === "telemetry") {
        if (second === "record") return json({ schema_version: 1, ok: true });
        if (second === "on" || second === "off") telemetry = second;
        return json({ schema_version: 1, enabled: telemetry === "on", available: true, saved_preference: telemetry,
          process_override: null, privacy_notice: "https://github.com/klovr-co/hover-tag/blob/main/docs/reference/telemetry.md" });
      }
      if (first === "version") {
        return json({ ...versionExample, version: runtimeVersion,
          capabilities: [...new Set([...versionExample.capabilities, "ai-connections", "shared-ai-connections", "logs-activity", "thinking-level", "describe", "telemetry-events"])] });
      }
      if (second === "settings" && args[2] === "ai") {
        const ai = aiFor(first, rows.find((r) => r.id === first));
        const [action, value] = args.slice(3).filter((a) => !a.startsWith("--"));
        if (!action) return json({ ...ai, running: rows.find((r) => r.id === first)?.state === "running" });
        if (action === "models") {
          await sleep(900);
          return json({ ...aiModelsExample, default: { ...ai.default_model, available: true, chosen: true },
            groups: aiModelsExample.groups.filter((g) => ai.usable.includes(g.backend)) });
        }
        if (action === "model" && value) {
          const entry = aiModelsExample.groups.flatMap((g) => g.models.map((m) => ({ ...m, group: g })))
            .find((m) => m.value === value);
          if (!entry) return { code: 1, stdout: JSON.stringify({ schema_version: 1, ok: false, error: `${value} isn't available` }), stderr: "" };
          ai.default_model = { value, backend: entry.group.backend, model: entry.model, label: entry.label,
            backend_name: entry.group.name, available: true };
          const asked = args.includes("--effort") ? args[args.indexOf("--effort") + 1] : null;
          const levels: string[] = entry.efforts ?? [];
          const effort = asked && asked !== "default" && levels.includes(asked) ? asked : entry.default_effort ?? null;
          Object.assign(ai, { default_effort: effort, effort_levels: levels, effort_chosen: !!asked && asked !== "default" });
          const row = rows.find((r) => r.id === first);
          if (row) Object.assign(row, { default_model: value, default_model_name: entry.label, default_effort: effort });
          const running = row?.state === "running";
          const restart = args.includes("--restart") && running;
          return json({ schema_version: 1, ok: true, default_model: ai.default_model, default_effort: effort,
            restart_required: running && !restart, restarted: restart });
        }
      }
      if (first === "autostart") {
        if (second === "on" || second === "off") keepRunning = second === "on";
        return json({ schema_version: 1, enabled: keepRunning, mechanism: "launchd",
          tags: rows.map((r) => ({ tag: r.id, keep_running: !!r.keep_running })) });
      }
      if (first === "upgrade") {
        const current = runtimeVersion;
        const dry = args.includes("--dry-run");
        const channel = args.includes("--channel") ? args[args.indexOf("--channel") + 1] : savedChannel;
        if (!dry) { runtimeVersion = targetVersion; savedChannel = channel; }
        return json({ schema_version: 1, ok: true,
          status: dry ? (current === targetVersion ? "current" : "available") : "upgraded",
          current: { version: current, channel: savedChannel, selection: "channel" },
          target: { version: targetVersion, channel } });
      }
      if ((first === "start" || first === "stop") && args.includes("--workspace")) {
        const team = args[args.indexOf("--workspace") + 1];
        rows.filter((r) => r.slack_workspace === team && r.state !== "setup_incomplete")
          .forEach((r) => { r.state = first === "start" ? "running" : "stopped"; r.keep_running = first === "start"; });
        return json({ schema_version: 1, ok: true, tags: [] });
      }
      const row = rows.find((r) => r.id === first);
      if (row && second === "logs") {
        if (args.includes("--activity")) {
          const item = activity[row.id]?.find((item) => item.run_id === args[args.indexOf("--activity") + 1]);
          return json({ schema_version: 1, ok: !!item, activity: item ? {
            run_id: item.run_id, outcome: "completed", started_at: item.at, finished_at: item.at,
            team: row.slack_workspace, channel: item.channel, thread_ts: "1791158400.000001", omitted: 0, error: null,
            events: [{ label: "Reading a file…", status: "completed", started_at: item.at, finished_at: item.at,
              details: { tool: "cat launch-plan.md", input: "Read launch-plan.md", output: "Launch checklist reviewed." } }],
          } : null });
        }
        const matches = (activity[row.id] ?? []).filter((item) =>
          (!args.includes("--activity-channel") || item.channel === args[args.indexOf("--activity-channel") + 1]) &&
          (!args.includes("--hide-errors") || item.kind !== "failed"))
          .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
        const limit = args.includes("--activity-limit") ? Number(args[args.indexOf("--activity-limit") + 1]) : 50;
        return json({ schema_version: 1, tag: row.id, services: {
          slack: ["Open Tag is live as @" + (row.slack_name ?? "Tag")],
          mfs: ["Memory server ready"] }, activity: matches.slice(0, limit), activity_has_more: matches.length > limit });
      }
      if (row && (second === "start" || second === "stop")) {
        row.state = second === "start" ? "running" : "stopped";
        row.keep_running = second === "start";
      }
      if (row && (second === "abandon" || second === "remove")) rows.splice(rows.indexOf(row), 1);
      if (row && second === "rename") {
        row.slack_name = args[2];
        row.nickname = args[2].toLowerCase().replace(/[^a-z0-9]+/g, "-");
      }
      if (row && second === "describe") row.description = args[2] || null;
      return { code: 0, stdout: "", stderr: "" };
    },
    follow: async (args, onLine, onExit) => {
      const row = rows.find((r) => r.id === args[0]);
      const lines = startExample.trim().split("\n");
      lines.forEach((line, i) => setTimeout(() => {
        onLine(line);
        if (i === lines.length - 1) {
          if (row) { row.state = "running"; row.keep_running = true; }
          onExit(0, "");
        }
      }, 700 * (i + 1)));
      return { send: () => {}, stop: () => {} };
    },
    setup: async (args, onLine, onExit) => {
      if (args[0] === "settings" && args[1] === "ai") return demoSignIn(sharedAI, ["", ...args], onLine, onExit);
      if (args[1] === "settings" && args[2] === "ai") return demoSignIn(aiFor(args[0]), args, onLine, onExit);
      const script = setupExample.trim().split("\n");
      let at = 0;
      const back: number[] = [];
      const next = () => {
        while (at < script.length) {
          const line = script[at++];
          onLine(line);
          if (line.includes('"type": "question"')) { back.push(at - 1); return; }
        }
        onExit(0, "");
      };
      setTimeout(next, 400);
      return {
        send: (message) => {
          const m = message as { back?: boolean; pause?: boolean };
          if (m.pause) { onLine('{"type": "result", "status": "paused"}'); onExit(0, ""); return; }
          if (m.back) { back.pop(); at = back.pop() ?? 0; }
          setTimeout(next, 500);
        },
        stop: () => onExit(0, ""),
      };
    },
    install: async (_channel, onLine, onExit) => {
      let cancelled = false;
      (async () => {
        for (const line of progressExample.trim().split("\n")) {
          await sleep(1400);
          if (cancelled) return;
          onLine(line);
          onLine(`  ${line.includes('"release"') ? "✓  Release   Tag v0.2.0" : "…"}`);
        }
        installed = true;
        onExit(0);
      })();
      return { send: () => {}, stop: () => { cancelled = true; onExit(130); } };
    },
    copy: async (text) => { await navigator.clipboard?.writeText(text).catch(() => {}); },
    paste: async () => "NwrFLzAy",
    open: async (target) => { console.info("open", target); },
    updateTray: async () => {},
    onTray: () => () => {},
    openAtLogin: async (enabled) => { if (enabled !== undefined) loginItem = enabled; return loginItem; },
    notify: async (title, body) => console.info("notify", title, body),
    showWindow: async () => {},
    fitWindow: async () => {},
    quit: async () => {},
    markMigrated: async () => {},
    markChannelInitialized: async () => {},
    checkAppUpdate: async () => desktopVersion !== targetVersion ? { version: targetVersion } : null,
    installAppUpdate: async () => { desktopVersion = targetVersion; await sleep(1500); },
    pickImage: async () => null,
    onFileDrop: () => () => {},
  };
}

// Sample AI connections, one copy per Tag so changes stick while the demo runs.
function demoAIFor(aiDemo: Map<string, AIStatus>, shared: AIStatus, tag: string, row?: TagRow): AIStatus {
  if (!aiDemo.has(tag)) {
    const ai = { ...structuredClone(aiStatusExample) as AIStatus, tag };
    const [backend, model] = (row?.default_model ?? "").split(":");
    if (backend && model) {
      ai.default_model = { ...ai.default_model, value: row!.default_model!, backend, model, label: row?.default_model_name ?? model,
        backend_name: backend === "claude" ? "Claude" : "Codex" };
      ai.default_effort = row?.default_effort ?? null;
    }
    aiDemo.set(tag, ai);
  }
  const ai = aiDemo.get(tag)!;
  ai.connections = shared.connections;
  ai.usable = shared.connections.filter((c) => c.state === "connected").map((c) => c.backend);
  return ai;
}

/** Plays `tag … settings ai sign-in`: progress, then connected unless cancelled. */
async function demoSignIn(ai: AIStatus, args: string[], onLine: (line: string) => void,
  onExit: (code: number, stderr: string) => void): Promise<Session> {
  const backend = args[3] === "resume" ? "codex" : args[4];
  const say = (event: object) => onLine(JSON.stringify(event));
  let cancelled = false;
  const finish = (status: string, connection?: Connection) => {
    say({ type: "sign_in", backend, status, connection, retry: status !== "connected",
      error: status === "cancelled" ? "Sign-in cancelled. Nothing changed." : undefined,
      restart_required: false, restarted: args.includes("--restart") || undefined });
    onExit(status === "connected" ? 0 : 1, "");
  };
  void (async () => {
    const steps = [...(args.includes("--restart") ? ["stopping"] : []), "browser", "waiting", "verifying",
      ...(args.includes("--restart") ? ["restarting"] : [])];
    const text: Record<string, string> = { stopping: "Stopping Tag while you sign in…", browser: "Opening your browser…",
      waiting: "Waiting for your browser…", verifying: "Finishing sign-in…", restarting: "Starting Tag again…" };
    for (const step of steps) {
      if (cancelled) return;
      say({ type: "progress", backend, step, text: text[step] });
      await sleep(step === "waiting" ? 2200 : 600);
    }
    if (cancelled) return;
    const index = ai.connections.findIndex((c) => c.backend === backend);
    const connection: Connection = { ...ai.connections[index], state: "connected", detail: "",
      account: backend === "claude" ? "Claude Max · maya@klovr.co" : args.includes("chatgpt") ? "ChatGPT plan · maya@klovr.co" : "ChatGPT sign-in",
      method: backend === "claude" ? "claude" : args.includes("chatgpt") ? "chatgpt" : "codex",
      shared: true, actions: ["change_account"] };
    ai.connections[index] = connection;
    ai.usable = ai.connections.filter((c) => c.state === "connected").map((c) => c.backend);
    finish("connected", connection);
  })();
  const cancel = () => { if (!cancelled) { cancelled = true; finish("cancelled"); } };
  return { send: (message) => { if ((message as { cancel?: boolean }).cancel) cancel(); }, stop: cancel };
}

let current: Promise<Bridge> | null = null;

export function bridge(): Promise<Bridge> {
  if (!current) {
    const params = new URLSearchParams(typeof location === "undefined" ? "" : location.search);
    current = inTauri
      // Sample data in the real app still sizes, shows and quits the real window.
      ? tauriBridge().then(async (real) => ((await real.info()).demo
        ? { ...demoBridge(), fitWindow: real.fitWindow, showWindow: real.showWindow, quit: real.quit, onFileDrop: real.onFileDrop }
        : real))
      : Promise.resolve(demoBridge({ installed: params.get("installed") !== "0" }));
    current = current.catch((error) => { current = null; throw error; });
  }
  return current;
}
