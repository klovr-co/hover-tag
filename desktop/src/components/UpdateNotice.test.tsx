// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProductUpdate, UpdateState } from "../lib/updates";
import { UpdateNotice } from "./UpdateNotice";

const found = (extra: Partial<ProductUpdate> = {}): ProductUpdate => ({
  version: "0.3.0", current: "0.3.0", runtime: false, desktop: false, channel: "stable", pinned: false, ahead: false, ...extra,
});
const notice = (state: UpdateState) =>
  render(<UpdateNotice state={state} outdated={false} run={vi.fn()} settings={vi.fn()} />);

describe("UpdateNotice", () => {
  afterEach(cleanup);

  it("tells you a release is on its way while its Tag.app builds are unpublished", () => {
    notice({ status: "current", update: found({ preparing: "0.4.0" }), phase: null, error: "" });
    expect(screen.getByText("Tag 0.4.0 is on its way")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("stays quiet when Tag is up to date", () => {
    const { container } = notice({ status: "current", update: found(), phase: null, error: "" });
    expect(container.firstChild).toBeNull();
  });

  const ready: UpdateState = { status: "available", update: found({ version: "0.4.0", runtime: true }), phase: null, error: "" };
  const defer = (deferred = false) => ({ deferred: vi.fn(() => deferred), later: vi.fn(), skip: vi.fn() });
  const withDefer = (state: UpdateState, d: ReturnType<typeof defer>, outdated = false) =>
    render(<UpdateNotice state={state} outdated={outdated} run={vi.fn()} settings={vi.fn()} defer={d} />);

  it("puts the ready banner off with Later", () => {
    const d = defer();
    withDefer(ready, d);
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    expect(d.later).toHaveBeenCalledWith("0.4.0");
    expect(d.skip).not.toHaveBeenCalled();
  });

  it("skips a version from the arrow beside Later", () => {
    const d = defer();
    withDefer(ready, d);
    expect(screen.queryByRole("menuitem")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /More ways/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Skip version" }));
    expect(d.skip).toHaveBeenCalledWith("0.4.0");
  });

  it("hides a banner that was put off, but never a required update", () => {
    const hidden = withDefer(ready, defer(true));
    expect(hidden.container.firstChild).toBeNull();
    cleanup();
    withDefer(ready, defer(true), true);
    expect(screen.getByText("Update Tag to continue")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Later" })).toBeNull();
  });
});
