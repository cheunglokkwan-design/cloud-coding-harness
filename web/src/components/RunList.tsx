import type { Run } from "../lib/types";
import { isTerminal } from "../lib/types";

export function elapsed(run: Run, now: number): string {
  if (!run.started_at) return "";
  const end = run.finished_at ?? now / 1000;
  const s = Math.max(0, end - run.started_at);
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function StatusDot({ run }: { run: Run }) {
  return <span className={`dot s-${run.status}`} title={run.status} aria-label={run.status} />;
}

export function RunList({ runs, selected, onSelect, now }: {
  runs: Run[]; selected: string | null; onSelect: (id: string) => void; now: number;
}) {
  if (!runs.length) return <p className="muted empty">No runs yet. Launch one above.</p>;
  return (
    <ul className="run-list">
      {runs.map((r) => (
        <li key={r.id}>
          <button className={`run-item ${selected === r.id ? "selected" : ""}`} onClick={() => onSelect(r.id)}>
            <StatusDot run={r} />
            <span className="run-main">
              <span className="run-task">{r.spec.task}</span>
              <span className="run-meta">
                {r.id.slice(4, 12)} · {isTerminal(r.status) ? r.status : r.phase ?? r.status}
                {r.startup ? ` · ready ${Math.round(r.startup.total_ms)}ms` : ""}
              </span>
            </span>
            <span className="run-time">{elapsed(r, now)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
