// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// Everything the UI asks of the operating system goes through here. In Tag.app
// it calls the Rust side (src-tauri); in a plain browser, or with
// TAG_INSTALLER_DEMO set, it plays sample data so nothing real changes.
import listExample from "../../../protocol/examples/list.json";
import type { TagRow } from "./protocol";
import versionExample from "../../../protocol/examples/version.json";
import progressExample from "../../../protocol/examples/install-progress.txt?raw";
import setupExample from "../../../protocol/examples/setup.jsonl?raw";

export interface AppInfo {
  platform: "macos" | "windows" | "linux" | string;
  demo: boolean;
  /** Path of the `tag` command, when Tag is installed. */
  cli: string | null;
  version: string;
  launchedAtLogin: boolean;
  /** Tags the earlier Mac app kept running at login, to carry over once. */
  legacyWantedTags: string[] | null;
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
  fitWindow(height: number): Promise<void>;
  quit(): Promise<void>;
  /** Record that the Swift app's login behaviour was carried over. */
  markMigrated(): Promise<void>;
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
    fitWindow: (height) => getCurrentWindow().setSize(new LogicalSize(520, Math.min(Math.max(height, 300), 860))),
    quit: () => invoke("quit"),
    markMigrated: () => invoke("mark_migrated"),
  };
}

// ---- Sample data -------------------------------------------------------------

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function demoBridge(options: { installed?: boolean } = {}): Bridge {
  const rows: (TagRow & { home: string })[] = structuredClone(listExample.tags);
  rows.splice(1, 0, {
    ...rows[0], id: "t0klovr1-a0rese02", slack_name: "Research Tag", state: "stopped",
    nickname: "research", main: false, keep_running: false,
  });
  rows.splice(2, 0, {
    ...rows[0], id: "t0acme01-a0ops003", slack_name: "Ops Tag", slack_workspace: "T0ACME01",
    workspace_name: "Acme Inc", main: false,
  });
  let installed = options.installed ?? true;
  let keepRunning = false;
  let loginItem = false;
  const json = (value: unknown): RunResult => ({ code: 0, stdout: JSON.stringify(value), stderr: "" });
  return {
    info: async () => ({
      platform: "macos", demo: true, cli: installed ? "~/.local/bin/tag" : null,
      version: "0.2.0", launchedAtLogin: false, legacyWantedTags: null,
    }),
    tag: async (args) => {
      await sleep(250);
      const [first, second] = args;
      if (first === "list") return json({ schema_version: 1, tags: rows });
      if (first === "version") return json(versionExample);
      if (first === "autostart") {
        if (second === "on" || second === "off") keepRunning = second === "on";
        return json({ schema_version: 1, enabled: keepRunning, mechanism: "launchd",
          tags: rows.map((r) => ({ tag: r.id, keep_running: !!r.keep_running })) });
      }
      if (first === "upgrade") {
        return json({ schema_version: 1, ok: true, status: "current", current: { version: "0.2.0" },
          target: { version: "0.2.0" } });
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
          slack: ["2026-10-02 09:14:03 Connected to Slack", "2026-10-02 09:20:41 Replied in #launch"],
          mfs: ["2026-10-02 09:14:01 Memory ready"] } });
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
    setup: async (_args, onLine, onExit) => {
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
  };
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
