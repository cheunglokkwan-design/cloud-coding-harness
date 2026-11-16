export type RunStatus = "queued" | "starting" | "running" | "succeeded" | "failed" | "cancelled" | "error";
export type Phase = "provision" | "analyze" | "generate" | "test" | "deliver";
export const PHASES: Phase[] = ["provision", "analyze", "generate", "test", "deliver"];

export interface StartupTiming {
  total_ms: number;
  container_ms: number;
  checkout_ms: number;
  warm: boolean;
  mirror_hit: boolean;
}

export interface Run {
  id: string;
  spec: { repo: string; ref: string | null; task: string; test_command: string | null; agent: { provider: string | null; model: string | null } };
  status: RunStatus;
  phase: Phase | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  sandbox_id: string | null;
  startup: StartupTiming | null;
  tests_passed: boolean | null;
  summary: string | null;
  error: string | null;
  files_changed: number;
  turns: number;
  input_tokens: number;
  output_tokens: number;
}

export interface HarnessEvent {
  seq: number;
  run_id: string;
  ts: number;
  type: string;
  phase: Phase | null;
  data: Record<string, any>;
}

export interface Summary {
  n: number;
  p50: number | null;
  p95: number | null;
  max: number | null;
}

export interface Metrics {
  runs: Record<RunStatus, number>;
  running: number;
  peak_running: number;
  queue_depth: number;
  max_concurrency: number;
  startup_ms: Summary;
  run_duration_s: Summary;
  sandbox: Record<string, any>;
  repo_cache: { hits: number; misses: number };
}

export const isTerminal = (s: RunStatus) => ["succeeded", "failed", "cancelled", "error"].includes(s);
