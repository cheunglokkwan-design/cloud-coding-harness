import { describe, expect, it } from "vitest";
import { parseUnifiedDiff, stripAnsi } from "./diff";
import { reduceEvents, emptyRunView } from "./runView";

const PATCH = `diff --git a/calc/stats.py b/calc/stats.py
index 1..2 100644
--- a/calc/stats.py
+++ b/calc/stats.py
@@ -10,4 +10,6 @@ def mean(values):
 def median(values):
+    if not values:
+        raise ValueError("empty")
     s = sorted(values)
-    return s[n // 2]
+    return s[0]
`;

describe("parseUnifiedDiff", () => {
  it("parses files, counts and line numbers", () => {
    const [f] = parseUnifiedDiff(PATCH);
    expect(f.path).toBe("calc/stats.py");
    expect(f.added).toBe(3);
    expect(f.removed).toBe(1);
    expect(f.lines[0].kind).toBe("hunk");
    expect(f.lines[1]).toMatchObject({ kind: "ctx", oldNo: 10, newNo: 10 });
    expect(f.lines[2]).toMatchObject({ kind: "add", newNo: 11 });
  });
});

describe("stripAnsi", () => {
  it("removes colour codes", () => {
    expect(stripAnsi("\u001b[31mred\u001b[0m")).toBe("red");
  });
});

describe("reduceEvents", () => {
  it("groups terminal output by command and tracks phases", () => {
    const ev = (seq: number, type: string, data: any, phase: any = "generate") =>
      ({ seq, run_id: "r", ts: seq, type, phase, data });
    const v = reduceEvents(emptyRunView(), [
      ev(1, "phase.started", {}, "analyze"),
      ev(2, "command.started", { command_id: "cmd1", command: "pytest" }),
      ev(3, "terminal", { command_id: "cmd1", stream: "stdout", chunk: "ok\n" }),
      ev(4, "command.finished", { command_id: "cmd1", exit_code: 0, duration_ms: 5 }),
      ev(5, "agent.thinking", { delta: "hmm " }),
      ev(6, "agent.thinking", { delta: "yes" }),
      ev(7, "phase.completed", { duration_ms: 12 }, "analyze"),
    ]);
    expect(v.commands[0]).toMatchObject({ command: "pytest", output: "ok\n", exitCode: 0 });
    expect(v.reasoning[0].thinking).toBe("hmm yes");
    expect(v.phases.analyze?.durationMs).toBe(12);
    expect(v.lastSeq).toBe(7);
  });
});
