// Folds the raw event stream of one run into the shapes each panel renders.
import type { HarnessEvent, Phase } from "./types";

export interface CommandView {
  id: string;
  command: string;
  phase: Phase | null;
  output: string;
  exitCode: number | null;
  durationMs: number | null;
  startedAt: number;
}

export interface ReasoningTurn {
  turn: number;
  thinking: string;
  text: string;
  model?: string;
  tokens?: { input: number; output: number };
}

export interface TraceItem {
  seq: number;
  ts: number;
  phase: Phase | null;
  kind: "phase" | "tool" | "test" | "sandbox" | "delivery" | "run" | "info";
  title: string;
  detail?: string;
  ok?: boolean;
  durationMs?: number;
}

export interface RunView {
  lastSeq: number;
  events: number;
  phases: Partial<Record<Phase, { startedAt: number; durationMs?: number }>>;
  trace: TraceItem[];
  reasoning: ReasoningTurn[];
  commands: CommandView[];
  diff: { patch: string; files: { path: string; added: string; removed: string }[] } | null;
  analysis: Record<string, any> | null;
  done: boolean;
}

export const emptyRunView = (): RunView => ({
  lastSeq: 0, events: 0, phases: {}, trace: [], reasoning: [], commands: [], diff: null, analysis: null, done: false,
});

const MAX_TERMINAL = 400_000;

function currentTurn(v: RunView): ReasoningTurn {
  if (!v.reasoning.length) v.reasoning.push({ turn: 1, thinking: "", text: "" });
  return v.reasoning[v.reasoning.length - 1];
}

function summarizeInput(name: string, input: Record<string, any>): string {
  if (name === "run_command") return input.command;
  if (input.path) return input.path;
  if (name === "search") return input.pattern;
  if (name === "finish") return input.summary;
  return JSON.stringify(input).slice(0, 120);
}

// Mutates a shallow copy; callers pass the previous view and a batch of new events.
export function reduceEvents(prev: RunView, events: HarnessEvent[]): RunView {
  const v: RunView = {
    ...prev,
    phases: { ...prev.phases },
    trace: prev.trace.slice(),
    reasoning: prev.reasoning.map((r) => ({ ...r })),
    commands: prev.commands.map((c) => ({ ...c })),
  };
  for (const e of events) {
    if (e.seq <= v.lastSeq) continue;
    v.lastSeq = e.seq;
    v.events++;
    const d = e.data;
    switch (e.type) {
      case "phase.started":
        if (e.phase) v.phases[e.phase] = { startedAt: e.ts };
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "phase", title: `${e.phase} started` });
        break;
      case "phase.completed":
        if (e.phase) v.phases[e.phase] = { ...(v.phases[e.phase] ?? { startedAt: e.ts }), durationMs: d.duration_ms };
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "phase", title: `${e.phase} completed`,
          durationMs: d.duration_ms });
        break;
      case "sandbox.ready":
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "sandbox", ok: true,
          title: `Sandbox ready in ${Math.round(d.total_ms)} ms`,
          detail: `${d.driver} · ${d.warm ? "warm pool" : "cold start"} · mirror ${d.mirror_hit ? "hit" : "miss"} · container ${Math.round(d.container_ms)} ms · checkout ${Math.round(d.checkout_ms)} ms · ${String(d.commit).slice(0, 10)}` });
        break;
      case "analysis":
        v.analysis = d;
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "info", title: `Analyzed ${d.file_count} files`,
          detail: `${Object.keys(d.languages ?? {}).join(", ") || "unknown languages"} · tests: ${d.test_command ?? "none"}` });
        break;
      case "agent.turn":
        v.reasoning.push({ turn: d.turn, thinking: "", text: "" });
        break;
      case "agent.thinking":
        currentTurn(v).thinking += d.delta;
        break;
      case "agent.text":
        currentTurn(v).text += d.delta;
        break;
      case "agent.message": {
        const t = currentTurn(v);
        // Streaming deltas already filled these in; fall back to the final text if not.
        if (!t.thinking && d.thinking) t.thinking = d.thinking;
        if (!t.text && d.text) t.text = d.text;
        t.model = d.model;
        t.tokens = { input: d.input_tokens, output: d.output_tokens };
        break;
      }
      case "tool.call":
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "tool", title: d.name,
          detail: summarizeInput(d.name, d.input) });
        break;
      case "tool.result": {
        for (let i = v.trace.length - 1; i >= 0; i--) {
          const it = v.trace[i];
          if (it.kind === "tool" && it.title === d.name && it.ok === undefined) {
            v.trace[i] = { ...it, ok: !d.is_error, durationMs: d.duration_ms };
            break;
          }
        }
        break;
      }
      case "command.started":
        v.commands.push({ id: d.command_id, command: d.command, phase: e.phase, output: "", exitCode: null,
          durationMs: null, startedAt: e.ts });
        break;
      case "terminal": {
        const c = v.commands.find((x) => x.id === d.command_id);
        if (c && c.output.length < MAX_TERMINAL) c.output += d.chunk;
        break;
      }
      case "command.finished": {
        const c = v.commands.find((x) => x.id === d.command_id);
        if (c) { c.exitCode = d.exit_code; c.durationMs = d.duration_ms; }
        break;
      }
      case "test.result":
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "test", ok: d.passed,
          title: `${d.baseline ? "Baseline tests" : "Tests"} ${d.passed ? "passed" : "failed"}`,
          detail: `${d.command} → exit ${d.exit_code}${d.timed_out ? " (timed out)" : ""}` });
        break;
      case "diff":
        v.diff = { patch: d.patch, files: d.files };
        break;
      case "delivery":
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "delivery", ok: d.files_changed > 0,
          title: d.files_changed ? `Patch delivered (${d.files_changed} file${d.files_changed > 1 ? "s" : ""})` : "No changes to deliver" });
        break;
      case "agent.budget_exhausted":
        v.trace.push({ seq: e.seq, ts: e.ts, phase: e.phase, kind: "info", ok: false, title: "Turn budget exhausted" });
        break;
      case "run.completed":
        v.done = true;
        v.trace.push({ seq: e.seq, ts: e.ts, phase: null, kind: "run", ok: d.status === "succeeded",
          title: `Run ${d.status}`, detail: d.error ?? d.summary ?? undefined });
        break;
    }
  }
  return v;
}
