import { afterEach, describe, expect, it } from "vitest";
import { claudeSharedNote, CODEX_METHODS } from "./ai";
import { machine, setPlatform } from "./machine";
import { heading } from "./setup";
import type { SetupQuestion } from "./protocol";

afterEach(() => setPlatform("macos"));

describe("naming this computer", () => {
  it("says Mac only on macOS", () => {
    expect(machine()).toBe("Mac");
    for (const platform of ["windows", "linux"]) {
      setPlatform(platform);
      expect(machine()).toBe("computer");
    }
  });

  it("words setup and AI settings for Windows", () => {
    setPlatform("windows");
    const question = { id: "ai_connection", kind: "choice", prompt: "", tag_name: "Windows" } as unknown as SetupQuestion;
    expect(heading(question)[1]).toBe("Set up Codex or Claude on this computer to continue.");
    expect(CODEX_METHODS.map((option) => option.title())).toContain("Codex sign-in on this computer");
    expect(claudeSharedNote()).toContain("Claude Code on this computer");
  });
});
