// Copyright 2026 klovr.co
// SPDX-License-Identifier: Apache-2.0
import { afterEach, describe, expect, it } from "vitest";
import { applyAppearance, savedAppearance, setAppearance } from "./appearance";

afterEach(() => { window.localStorage.clear(); delete document.documentElement.dataset.theme; });

describe("appearance", () => {
  it("defaults to auto and leaves the theme to the system", () => {
    expect(savedAppearance()).toBe("auto");
    applyAppearance(savedAppearance());
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
  it("remembers a forced light or dark choice", () => {
    setAppearance("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(savedAppearance()).toBe("dark");
    setAppearance("light");
    expect(savedAppearance()).toBe("light");
  });
  it("returns to auto", () => {
    setAppearance("dark");
    setAppearance("auto");
    expect(savedAppearance()).toBe("auto");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
});
