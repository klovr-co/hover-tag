// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// What an add-on's code gets from Tag.app (interface 1), and what it gives back.
// An add-on reads Tag data only through `tag … --json`; it never gets file access.
import type { RunResult } from "../bridge";
import type { Manifest } from "./manifest";

/** Where a thread or Tag button was pressed, or which channel a switch is for. */
export interface ThreadTarget { tag: string; channel: string; thread_ts: string }
export interface TagTarget { tag: string }
export interface ChannelTarget { tag: string; channel: { id: string; name: string | null } }

export interface Folder { id: string; title: string; icon?: string; detail?: string }

/** Draws into `element` and returns a clean-up function. Framework-free on purpose. */
export type Mount<T> = (element: HTMLElement, context: T) => void | (() => void);

/** The add-on module's `activate(host)` result, keyed by the ids its manifest declares. */
export interface Contributions {
  screens?: Record<string, { mount: Mount<Record<string, never>> }>;
  knowledge_folders?: Record<string, {
    list(target: TagTarget): Promise<Folder[]>;
    mount: Mount<{ tag: string; folder: Folder }>;
  }>;
  buttons?: Record<string, { run(target: ThreadTarget | TagTarget): void | Promise<void> }>;
  channel_switches?: Record<string, {
    get(target: ChannelTarget): Promise<boolean>;
    set(target: ChannelTarget, on: boolean): Promise<void>;
  }>;
}

export interface AddonHost {
  interface: 1;
  app: { version: string; platform: string };
  account: { label: string };
  /** Runs `tag ARGS`; only commands the manifest lists, and always with `--json`. */
  tag(args: string[]): Promise<RunResult>;
  /** A request to the provider, with this computer's token. `path` starts with "/". */
  request(path: string, init?: RequestInit): Promise<Response>;
  /** Opens an http(s) page in the browser. */
  open(url: string): Promise<void>;
  /** A short message at the bottom of the window. */
  say(text: string): void;
}

/** The first argument that isn't a Tag name or an option names the command: `tag list`, `tag NAME threads`. */
export function allowedTagCall(args: string[], manifest: Pick<Manifest, "tag_commands">, tags: string[]) {
  if (!args.includes("--json")) return false;
  const words = args.filter((a) => !a.startsWith("-"));
  const command = words[0] && tags.includes(words[0]) ? words[1] : words[0];
  return !!command && manifest.tag_commands.includes(command);
}

export function makeHost(options: {
  manifest: Manifest; account: { label: string }; providerUrl: string; token: string;
  app: { version: string; platform: string };
  run: (args: string[]) => Promise<RunResult>; tagIds: () => string[];
  open: (url: string) => Promise<void>; say: (text: string) => void;
}): AddonHost {
  const base = options.providerUrl.replace(/\/+$/, "");
  return Object.freeze({
    interface: 1 as const,
    app: { ...options.app },
    account: { ...options.account },
    tag: async (args: string[]) => {
      if (!Array.isArray(args) || !args.every((a) => typeof a === "string")) throw new Error("tag() takes a list of strings.");
      if (!allowedTagCall(args, options.manifest, options.tagIds())) {
        return { code: 2, stdout: "", stderr: `${options.manifest.name} may not run: tag ${args.join(" ")}` };
      }
      return options.run([...args]);
    },
    request: (path: string, init: RequestInit = {}) => {
      if (!path.startsWith("/") || path.startsWith("//")) return Promise.reject(new Error("request() takes a path on the provider."));
      const headers = new Headers(init.headers);
      headers.set("authorization", `Bearer ${options.token}`);
      return fetch(base + path, { ...init, headers });
    },
    open: (url: string) => /^https?:\/\//.test(url) ? options.open(url) : Promise.reject(new Error("open() takes an http(s) address.")),
    say: (text: string) => options.say(String(text).slice(0, 200)),
  });
}
