import type { Run } from "../lib/types";
import { PHASES, isTerminal } from "../lib/types";

/** One tile per agent, coloured by state: an at-a-glance view of a large concurrent batch. */
export function Fleet({ runs, onSelect }: { runs: Run[]; onSelect: (id: string) => void }) {
  const counts: Record<string, number> = {};
  for (const r of runs) {
    const k = isTerminal(r.status) ? r.status : r.phase ?? r.status;
    counts[k] = (counts[k] ?? 0) + 1;
  }
  const legend = ["queued", ...PHASES, "succeeded", "failed", "error", "cancelled"].filter((k) => counts[k]);
  return (
    <section className="card fleet">
      <div className="fleet-head">
        <h2>Fleet</h2>
        <span className="muted small">{runs.length} agents</span>
      </div>
      <div className="legend">
        {legend.map((k) => (
          <span key={k} className="legend-item"><i className={`tile t-${k}`} />{k} <b>{counts[k]}</b></span>
        ))}
      </div>
      <div className="tiles" role="list">
        {runs.slice().reverse().map((r) => {
          const k = isTerminal(r.status) ? r.status : r.phase ?? r.status;
          return (
            <button key={r.id} role="listitem" className={`tile t-${k}`} title={`${r.id} · ${k}\n${r.spec.task}`}
              onClick={() => onSelect(r.id)} aria-label={`${r.id} ${k}`} />
          );
        })}
      </div>
      {!runs.length && <p className="muted">Launch a batch (set Agents to 100+) to watch the fleet fill up.</p>}
    </section>
  );
}
