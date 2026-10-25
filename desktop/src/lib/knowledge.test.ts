// The Knowledge tab's helpers read the same protocol examples the CLI tests check.
import { describe, expect, it } from "vitest";
import listExample from "../../../protocol/examples/knowledge-list.json";
import readExample from "../../../protocol/examples/knowledge-read.json";
import changeExample from "../../../protocol/examples/knowledge-change.json";
import historyExample from "../../../protocol/examples/knowledge-history.json";
import showExample from "../../../protocol/examples/knowledge-show.json";
import {
  addArgs, addProblem, blocks, coreBody, demoKnowledge, detailLine, editArgs, findNote, inline, keyFrom, knowledgeError, summaryText,
  threadLink, who, withEntry, type Change, type ChangeDetail, type KnowledgeFile, type KnowledgeList,
} from "./knowledge";

const list = listExample as unknown as KnowledgeList;
const file = readExample.file as unknown as KnowledgeFile;

describe("protocol examples", () => {
  it("parse into what the tab shows", () => {
    expect(list.channels[0].core?.entries).toBe(2);
    expect(file.entries?.map((e) => e.key)).toEqual(["access-approval", "digest-format"]);
    expect((changeExample.change as Change).via).toBe("tag-app");
    expect((historyExample.changes as Change[]).every((c) => typeof c.undoable === "boolean")).toBe(true);
    expect((showExample as unknown as ChangeDetail).files[0].diff.some((l) => l.op === "+")).toBe(true);
  });
});

describe("core.md bodies", () => {
  it("are rebuilt with one entry changed, the way Tag writes them", () => {
    expect(withEntry(file.entries!, "digest-format", "Five bullets.")).toBe(
      `## access-approval\n${file.entries![0].text}\n\n## digest-format\nFive bullets.\n`);
    expect(file.body).toBe(coreBody(file.entries!));
  });

  it("forgetting an entry drops its section", () => {
    expect(withEntry(file.entries!, "access-approval", null)).toBe(`## digest-format\n${file.entries![1].text}\n`);
  });

  it("escape text lines that look like a section", () => {
    expect(coreBody([{ key: "a", text: "## not a key\nok" }])).toBe("## a\n\\## not a key\nok\n");
  });
});

describe("commands", () => {
  it("pass text with = so a leading dash stays text", () => {
    expect(editArgs("t1", "memory/core.md", "v1", "- one")).toContain("--body=- one");
    expect(addArgs("t1", { key: "x", text: "-5 degrees", kind: "note", channel: "" })).toEqual(
      ["t1", "knowledge", "add", "--key=x", "--text=-5 degrees", "--kind", "note", "--all-channels", "--via", "tag-app", "--json"]);
    expect(addArgs("t1", { key: "x", text: "y", kind: "core", channel: "C1" })).toContain("--channel");
  });

  it("check a new entry before sending it", () => {
    const limits = list.limits;
    expect(addProblem({ key: "Bad Key", text: "x", kind: "core", channel: "" }, limits)).toMatch(/short name/);
    expect(addProblem({ key: "ok", text: " ", kind: "core", channel: "" }, limits)).toMatch(/Write what/);
    expect(addProblem({ key: "ok", text: "x".repeat(4001), kind: "note", channel: "" }, limits)).toMatch(/4,000/);
    expect(addProblem({ key: "ok", text: "fine", kind: "note", channel: "" }, limits)).toBe("");
  });

  it("suggest a key from the text", () => {
    expect(keyFrom("The weekly report is due Friday!")).toBe("the-weekly-report-is");
    expect(keyFrom("Café opens")).toBe("cafe-opens");
  });

  it("read the CLI's error and code", () => {
    expect(knowledgeError('{"ok": false, "error": "Stale", "code": "conflict"}', "")).toEqual({ error: "Stale", code: "conflict" });
    expect(knowledgeError("", "Error: boom\n")).toEqual({ error: "boom", code: "" });
  });
});

describe("words", () => {
  it("say who and where", () => {
    expect(who("local-operator")).toBe("you, in Tag.app");
    expect(who("U0MAYA001")).toBe("U0MAYA001 in Slack");
    expect(detailLine(file)).toMatch(/^#ops · always loaded · 208 \/ 6k · changed /);
    expect(summaryText("Saved cto for channel C0OPS0001", list.channels)).toBe("Saved cto for #ops");
  });

  it("read a thread link", () => {
    expect(threadLink("hover-tag://tag/t1/thread/C0OPS0001/1760600000.000200")).toEqual({ tag: "t1", channel: "C0OPS0001", ts: "1760600000.000200" });
    expect(threadLink("https://example.com")).toBeNull();
  });
});

describe("markdown", () => {
  it("reads only what memory needs, as text", () => {
    expect(inline("See [[digest-sources]] and **this** `code` [site](https://example.com) <b>")).toEqual([
      { t: "text", v: "See " }, { t: "key", v: "digest-sources" }, { t: "text", v: " and " }, { t: "b", v: "this" },
      { t: "text", v: " " }, { t: "code", v: "code" }, { t: "text", v: " " }, { t: "link", v: "site", href: "https://example.com" },
      { t: "text", v: " <b>" }]);
    expect(blocks("# Title\n\nOne\ntwo\n\n- a\n- b\n1. c").map((b) => b.t)).toEqual(["h", "p", "ul", "ol"]);
  });

  it("finds a linked note in the channel first", () => {
    expect(findNote(list, "digest-sources", "C0OPS0001")?.path).toBe("memory/channels/C0OPS0001/notes/digest-sources.md");
    expect(findNote(list, "missing", "C0OPS0001")).toBeNull();
  });
});

describe("demo", () => {
  it("edits, refuses a stale edit, and undoes", () => {
    const run = demoKnowledge();
    const read = run(["read", "memory/channels/C0OPS0001/core.md"]) as { file: KnowledgeFile };
    const edited = run(["edit", read.file.path, `--body=${withEntry(read.file.entries!, "digest-format", "Five bullets.")}`, "--base", read.file.version]) as { ok: boolean; change: Change };
    expect(edited.ok).toBe(true);
    expect(run(["edit", read.file.path, "--body=x", "--base", read.file.version])).toMatchObject({ ok: false, code: "conflict" });
    expect(run(["undo", edited.change.id])).toMatchObject({ ok: true });
    expect((run(["read", read.file.path]) as { file: KnowledgeFile }).file.body).toBe(read.file.body);
  });
});
