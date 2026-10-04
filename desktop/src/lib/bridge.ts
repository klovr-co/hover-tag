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
import aiStatusExample from "../../../protocol/examples/ai-status.json";
import aiModelsExample from "../../../protocol/examples/ai-models.json";
import type { AIStatus, Connection } from "./ai";

export interface AppInfo {
  platform: "macos" | "windows" | "linux" | string;
  demo: boolean;
  /** Path of the `tag` command, when Tag is installed. */
  cli: string | null;
  version: string;
  launchedAtLogin: boolean;
  /** Tags the earlier Mac app kept running at login, to carry over once. */
  legacyWantedTags: string[] | null;
  /** The person's first name from their computer account, for Home's greeting. */
  firstName?: string | null;
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
  /** A newer signed Tag.app on `channel` (stable, beta, or alpha); otherwise on this build's own release line. */
  checkAppUpdate(channel?: string): Promise<AppUpdate | null>;
  /** Install the update found by checkAppUpdate and restart into it. */
  installAppUpdate(version: string): Promise<void>;
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
    fitWindow: (width, height) => getCurrentWindow().setSize(new LogicalSize(width, Math.min(Math.max(height, 300), 860))),
    quit: () => invoke("quit"),
    markMigrated: () => invoke("mark_migrated"),
    checkAppUpdate: (channel) => invoke<AppUpdate | null>("app_update_check", { channel: channel ?? null }),
    installAppUpdate: (version) => invoke("app_update_install", { version }),
  };
}

// ---- Sample data -------------------------------------------------------------

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function demoBridge(options: { installed?: boolean } = {}): Bridge {
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
  const activity: Record<string, { at: string; kind: string; channel: string; channel_name: string | null; dm: boolean }[]> = {
    [rows[0].id]: [{ at: today(9, 20), kind: "replied", channel: "C0LAUNCH", channel_name: "launch", dm: false }],
    "t0acme01-a0ops003": [{ at: today(10, 12), kind: "replied", channel: "C0LAUNCHOPS", channel_name: "launch-ops", dm: false }],
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
  const json = (value: unknown): RunResult => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
  return {
    info: async () => ({
      platform: "macos", demo: true, cli: installed ? "~/.local/bin/tag" : null,
      version: desktopVersion, launchedAtLogin: false, legacyWantedTags: null, firstName: "Maya",
    }),
    tag: async (args) => {
      await sleep(250);
      const [first, second] = args;
      if (first === "list") return json({ schema_version: 1, tags: rows });
      if (first === "version") {
        return json({ ...versionExample, version: runtimeVersion,
          capabilities: [...new Set([...versionExample.capabilities, "ai-connections", "logs-activity", "thinking-level"])] });
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
        return json({ schema_version: 1, tag: row.id, services: {
          slack: ["Open Tag is live as @" + (row.slack_name ?? "Tag")],
          mfs: ["Memory server ready"] }, activity: activity[row.id] ?? [] });
      }
      if (row && (second === "start" || second === "stop")) {
        row.state = second === "start" ? "running" : "stopped";
        row.keep_running = second === "start";
      }
      if (row && second === "rename") {
        row.slack_name = args[2];
        row.nickname = args[2].toLowerCase().replace(/[^a-z0-9]+/g, "-");
      }
      return { code: 0, stdout: "", stderr: "" };
    },
    setup: async (args, onLine, onExit) => {
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
    checkAppUpdate: async () => desktopVersion !== targetVersion ? { version: targetVersion } : null,
    installAppUpdate: async () => { desktopVersion = targetVersion; await sleep(1500); },
  };
}

// Sample AI connections, one copy per Tag so changes stick while the demo runs.
const aiDemo = new Map<string, AIStatus>();
function aiFor(tag: string, row?: TagRow): AIStatus {
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
  return aiDemo.get(tag)!;
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
      shared: !args.includes("chatgpt"), actions: ["change_account"] };
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
      ? tauriBridge().then(async (real) => ((await real.info()).demo ? demoBridge() : real))
      : Promise.resolve(demoBridge({ installed: params.get("installed") !== "0" }));
  }
  return current;
}
