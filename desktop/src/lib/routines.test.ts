// Routines: the app reads the shared examples and sends the CLI only what changed.
import { describe, expect, it } from "vitest";
import routineList from "../../../protocol/examples/routine-list.json";
import routineShow from "../../../protocol/examples/routine-show.json";
import { cliError, emptyForm, formArgs, formOf, formProblem, outcomeGood, outcomeText, routineLine, triggerText, type Routine, type RoutineDetail, type RoutineList } from "./routines";

const list = routineList as unknown as RoutineList;
const routine = list.routines[0] as Routine;

describe("routine examples", () => {
  it("reads the list and show examples", () => {
    expect(list.routines.map((r) => r.name)).toEqual(["Weekly PR digest"]);
    const detail = routineShow as unknown as RoutineDetail;
    expect(detail.history.map(outcomeText)).toEqual(["Missed"]);
    expect(outcomeGood(detail.history[0])).toBe(false);
    expect(outcomeGood(routine.last_run!)).toBe(true);
  });
});

describe("routine line", () => {
  it("says when it runs next, or why it won't", () => {
    expect(routineLine(routine, true)).toMatch(/^mon 09:00 · #launch · next /);
    expect(routineLine(routine, false)).toBe("mon 09:00 · #launch · waits for the Tag to start");
    expect(routineLine({ ...routine, enabled: false }, true)).toBe("mon 09:00 · #launch · paused");
    expect(routineLine({ ...routine, enabled: false, paused_reason: "Paused after 5 failed runs in a row." }, true))
      .toBe("mon 09:00 · #launch · Paused after 5 failed runs in a row.");
    expect(routineLine({ ...routine, approved: false }, true)).toBe("mon 09:00 · #launch · changed since it was approved");
    expect(routineLine({ ...routine, running: true }, true)).toBe("mon 09:00 · #launch · running now");
    expect(outcomeText({ ...routine.last_run!, outcome: "missed", count: 3 })).toBe("Missed 3 runs");
    expect(outcomeText({ ...routine.last_run!, outcome: "something-new" })).toBe("Needs attention");
    const stopped = { ...routine.last_run!, outcome: "cancelled", trigger: "retry" };
    expect([outcomeText(stopped), triggerText(stopped), outcomeGood(stopped)]).toEqual(["Stopped", " · tried again", true]);
  });
});

describe("routine form", () => {
  it("adds with every field and edits only what changed", () => {
    const form = { ...emptyForm("C0LAUNCH1"), name: " Digest ", prompt: "Summarise.", timezone: "Europe/London" };
    expect(formArgs("t1", form)).toEqual(["t1", "routine", "add", "--name", "Digest", "--schedule", "weekdays 09:00",
      "--timezone", "Europe/London", "--channel", "C0LAUNCH1", "--prompt", "Summarise.", "--max-budget-usd", "0.50", "--via", "tag-app", "--json"]);
    const edited = { ...formOf(routine), schedule: "fri 17:30" };
    expect(formArgs("t1", edited, routine)).toEqual(["t1", "routine", "edit", routine.id, "--schedule", "fri 17:30", "--via", "tag-app", "--json"]);
  });

  it("asks for what's missing before calling the CLI", () => {
    expect(formProblem(emptyForm("C1"))).toBe("Give the routine a name.");
    expect(formProblem({ ...emptyForm(""), name: "x", prompt: "p" })).toBe("Choose a channel.");
    expect(formProblem({ ...emptyForm("C1"), name: "x" })).toBe("Say what the Tag should do each time.");
    expect(formProblem({ ...emptyForm("C1"), name: "x", prompt: "p", budget: "50" })).toMatch(/spend limit/);
    expect(formProblem({ ...emptyForm("C1"), name: "x", prompt: "p" })).toBe("");
  });

  it("shows the CLI's own error", () => {
    expect(cliError(JSON.stringify({ schema_version: 1, ok: false, error: "Routines run at most once an hour" }), "")).toBe("Routines run at most once an hour");
    expect(cliError("", "Traceback\nError: Start the Tag first")).toBe("Start the Tag first");
  });
});
