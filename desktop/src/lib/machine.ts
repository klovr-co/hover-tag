// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
// What to call this computer in copy such as "Connect to this Mac".

let platform = "macos";

/** Tag.app sets this once it knows where it runs, before any screen that names the computer. */
export function setPlatform(value: string) {
  platform = value;
}

/** "Mac" on macOS and "computer" on Windows and Linux. */
export const machine = () => (platform === "macos" ? "Mac" : "computer");
