import { useEffect, useRef, useState } from "react";
import type { HarnessEvent, Metrics, Run } from "./types";
import { emptyRunView, reduceEvents, type RunView } from "./runView";

export const API = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export async function createRun(body: Record<string, unknown>, count = 1): Promise<string[]> {
  const url = count > 1 ? `${API}/api/runs/batch` : `${API}/api/runs`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(count > 1 ? { ...body, count } : body),
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  const json = await res.json();
  return count > 1 ? json.ids : [json.id];
}

export async function cancelRun(id: string) {
  await fetch(`${API}/api/runs/${id}/cancel`, { method: "POST" });
}

/** Run list + metrics, kept live by the global SSE stream. */
export function useFleet() {
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API}/api/runs`).then((r) => r.json()).then((list: Run[]) => {
      if (cancelled) return;
      setRuns((prev) => {
        const next = { ...prev };
        for (const r of list) if (!next[r.id]) next[r.id] = r;
        return next;
      });
    }).catch(() => undefined);

    const pending: Run[] = [];
    let frame = 0;
    const flush = () => {
      frame = 0;
      const batch = pending.splice(0);
      setRuns((prev) => {
        const next = { ...prev };
        for (const r of batch) next[r.id] = r;
        return next;
      });
    };
    const es = new EventSource(`${API}/api/stream`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "metrics") setMetrics(msg.metrics);
      else if (msg.type === "run") {
        pending.push(msg.run);
        if (!frame) frame = requestAnimationFrame(flush);
      }
    };
    return () => {
      cancelled = true;
      es.close();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return { runs, metrics, connected };
}

/** Event stream of one run, folded into a RunView. Reconnects resume from the last seq. */
export function useRunView(runId: string | null): RunView {
  const [view, setView] = useState<RunView>(emptyRunView);
  const buf = useRef<HarnessEvent[]>([]);

  useEffect(() => {
    setView(emptyRunView());
    buf.current = [];
    if (!runId) return;
    let frame = 0;
    const flush = () => {
      frame = 0;
      const batch = buf.current.splice(0);
      if (batch.length) setView((v) => reduceEvents(v, batch));
    };
    const es = new EventSource(`${API}/api/runs/${runId}/events`);
    es.onmessage = (m) => {
      buf.current.push(JSON.parse(m.data));
      if (!frame) frame = requestAnimationFrame(flush);
    };
    es.addEventListener("end", () => es.close());
    return () => {
      es.close();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [runId]);

  return view;
}
