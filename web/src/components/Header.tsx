import type { Metrics } from "../lib/types";

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="stat">
      <span className="stat-value" data-tone={tone}>{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

const ms = (v: number | null | undefined) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);

export function Header({ metrics, connected }: { metrics: Metrics | null; connected: boolean }) {
  const m = metrics;
  return (
    <header className="header">
      <div className="brand">
        <span className="logo" aria-hidden>▸_</span>
        <div>
          <h1>Coding Harness</h1>
          <span className={`conn ${connected ? "on" : "off"}`}>{connected ? "live" : "reconnecting"}</span>
          {m && <span className="muted small"> · {String(m.sandbox.driver ?? "")} sandboxes</span>}
        </div>
      </div>
      <div className="stats">
        <Stat label="running" value={m?.running ?? "–"} tone="running" />
        <Stat label="queued" value={m?.queue_depth ?? "–"} />
        <Stat label="peak" value={m?.peak_running ?? "–"} />
        <Stat label="succeeded" value={m?.runs.succeeded ?? "–"} tone="ok" />
        <Stat label="failed" value={m ? m.runs.failed + m.runs.error : "–"} tone="bad" />
        <Stat label="startup p50" value={ms(m?.startup_ms.p50)} />
        <Stat label="startup p95" value={ms(m?.startup_ms.p95)} />
        <Stat label="warm ready" value={m ? `${m.sandbox.pool_ready ?? 0}/${m.sandbox.pool_target ?? 0}` : "–"} />
      </div>
    </header>
  );
}
