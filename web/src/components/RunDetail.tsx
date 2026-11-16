import { useEffect, useMemo, useRef, useState } from "react";
import { API, cancelRun, useRunView } from "../lib/api";
import { parseUnifiedDiff, stripAnsi } from "../lib/diff";
import type { RunView } from "../lib/runView";
import { PHASES, isTerminal, type Run } from "../lib/types";
import { StatusDot, elapsed } from "./RunList";

type Tab = "trace" | "reasoning" | "terminal" | "changes";

export function RunDetail({ run, now, onBack }: { run: Run; now: number; onBack: () => void }) {
  const view = useRunView(run.id);
  const [tab, setTab] = useState<Tab>("trace");
  const tabs: [Tab, string, number | null][] = [
    ["trace", "Trace", view.trace.length],
    ["reasoning", "Reasoning", view.reasoning.length],
    ["terminal", "Terminal", view.commands.length],
    ["changes", "Changes", view.diff?.files.length ?? 0],
  ];
  return (
    <section className="card detail">
      <div className="detail-head">
        <button className="link" onClick={onBack}>← Fleet</button>
        <div className="detail-title">
          <StatusDot run={run} />
          <h2>{run.spec.task}</h2>
        </div>
        <div className="detail-meta muted small">
          <span>{run.id}</span>
          <span>{run.spec.repo}</span>
          <span>{run.spec.agent.provider ?? "default agent"}</span>
          <span>{elapsed(run, now)}</span>
          {run.turns > 0 && <span>{run.turns} turns</span>}
          {run.output_tokens > 0 && <span>{(run.input_tokens + run.output_tokens).toLocaleString()} tokens</span>}
        </div>
        <div className="detail-actions">
          {!isTerminal(run.status) && <button className="secondary" onClick={() => cancelRun(run.id)}>Cancel</button>}
          {run.status === "succeeded" && <a className="button" href={`${API}/api/runs/${run.id}/patch`}>Download patch</a>}
        </div>
      </div>
      <PhaseBar run={run} view={view} />
      {run.error && <p className="error">{run.error}</p>}
      <div className="tabs" role="tablist">
        {tabs.map(([id, label, n]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            {label}{n ? <span className="count">{n}</span> : null}
          </button>
        ))}
      </div>
      <div className="tab-body">
        {tab === "trace" && <TracePanel view={view} />}
        {tab === "reasoning" && <ReasoningPanel view={view} />}
        {tab === "terminal" && <TerminalPanel view={view} />}
        {tab === "changes" && <ChangesPanel view={view} />}
      </div>
    </section>
  );
}

function PhaseBar({ run, view }: { run: Run; view: RunView }) {
  const current = isTerminal(run.status) ? null : run.phase;
  return (
    <ol className="phases">
      {PHASES.map((p) => {
        const info = view.phases[p];
        const state = p === current ? "active" : info?.durationMs != null ? "done" : info ? "active" : "todo";
        return (
          <li key={p} className={`phase ${state}`}>
            <span className="phase-name">{p}</span>
            <span className="phase-time">{info?.durationMs != null ? `${(info.durationMs / 1000).toFixed(2)}s` : state === "active" ? "…" : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

function useAutoScroll<T extends HTMLElement>(dep: unknown) {
  const ref = useRef<T>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [dep]);
  const onScroll = () => {
    const el = ref.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  return { ref, onScroll };
}

function TracePanel({ view }: { view: RunView }) {
  const { ref, onScroll } = useAutoScroll<HTMLOListElement>(view.trace.length);
  const t0 = view.trace[0]?.ts ?? 0;
  if (!view.trace.length) return <p className="muted">Waiting for the run to start…</p>;
  return (
    <ol className="trace scroll" ref={ref} onScroll={onScroll}>
      {view.trace.map((it) => (
        <li key={it.seq} className={`trace-item k-${it.kind} ${it.ok === false ? "bad" : it.ok ? "ok" : ""}`}>
          <span className="trace-t">+{(it.ts - t0).toFixed(2)}s</span>
          <span className="trace-badge">{it.kind === "tool" ? "tool" : it.phase ?? it.kind}</span>
          <span className="trace-body">
            <span className="trace-title">{it.title}</span>
            {it.detail && <span className="trace-detail">{it.detail}</span>}
          </span>
          <span className="trace-dur">{it.durationMs != null ? `${Math.round(it.durationMs)} ms` : ""}</span>
        </li>
      ))}
    </ol>
  );
}

function ReasoningPanel({ view }: { view: RunView }) {
  const { ref, onScroll } = useAutoScroll<HTMLDivElement>(view.reasoning.map((r) => r.thinking.length + r.text.length).join());
  if (!view.reasoning.length) return <p className="muted">The agent has not started reasoning yet.</p>;
  return (
    <div className="reasoning scroll" ref={ref} onScroll={onScroll}>
      {view.reasoning.map((r, i) => (
        <article key={i} className="turn">
          <header className="muted small">
            Turn {r.turn}{r.model ? ` · ${r.model}` : ""}{r.tokens && r.tokens.output ? ` · ${r.tokens.input.toLocaleString()} in / ${r.tokens.output.toLocaleString()} out` : ""}
          </header>
          {r.thinking && <p className="thinking">{r.thinking}</p>}
          {r.text && <p className="say">{r.text}</p>}
        </article>
      ))}
    </div>
  );
}

function TerminalPanel({ view }: { view: RunView }) {
  const total = view.commands.reduce((n, c) => n + c.output.length, 0);
  const { ref, onScroll } = useAutoScroll<HTMLDivElement>(total + view.commands.length);
  if (!view.commands.length) return <p className="muted">No commands yet.</p>;
  return (
    <div className="terminal scroll" ref={ref} onScroll={onScroll}>
      {view.commands.map((c) => (
        <div key={c.id} className="cmd">
          <div className="cmd-line">
            <span className="prompt">$</span> {c.command}
            <span className={`exit ${c.exitCode === 0 ? "ok" : c.exitCode == null ? "" : "bad"}`}>
              {c.exitCode == null ? "running…" : `exit ${c.exitCode} · ${Math.round(c.durationMs ?? 0)} ms`}
            </span>
          </div>
          <pre>{stripAnsi(c.output)}</pre>
        </div>
      ))}
    </div>
  );
}

function ChangesPanel({ view }: { view: RunView }) {
  const files = useMemo(() => parseUnifiedDiff(view.diff?.patch ?? ""), [view.diff?.patch]);
  if (!files.length) return <p className="muted">No changes yet.</p>;
  return (
    <div className="changes scroll">
      {files.map((f) => (
        <div key={f.path} className="diff-file">
          <div className="diff-head">
            <span className="mono">{f.path}</span>
            <span><span className="add">+{f.added}</span> <span className="del">−{f.removed}</span></span>
          </div>
          {f.binary ? <p className="muted small">Binary file</p> : (
            <table className="diff">
              <tbody>
                {f.lines.map((l, i) => (
                  <tr key={i} className={`dl-${l.kind}`}>
                    <td className="ln">{l.oldNo ?? ""}</td>
                    <td className="ln">{l.newNo ?? ""}</td>
                    <td className="code"><span className="sign">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : " "}</span>{l.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </div>
  );
}
