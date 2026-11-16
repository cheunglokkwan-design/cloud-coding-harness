import { useEffect, useMemo, useState } from "react";
import { useFleet } from "./lib/api";
import { Header } from "./components/Header";
import { NewRun } from "./components/NewRun";
import { RunList } from "./components/RunList";
import { Fleet } from "./components/Fleet";
import { RunDetail } from "./components/RunDetail";

function useNow(ms = 500) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

function readHash() {
  const m = location.hash.match(/^#\/runs\/(run_[a-z0-9]+)/);
  return m ? m[1] : null;
}

export default function App() {
  const { runs, metrics, connected } = useFleet();
  const [selected, setSelected] = useState<string | null>(readHash);
  const now = useNow();

  useEffect(() => {
    const onHash = () => setSelected(readHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);

  const select = (id: string | null) => {
    location.hash = id ? `/runs/${id}` : "";
    setSelected(id);
  };

  const list = useMemo(() => Object.values(runs).sort((a, b) => b.created_at - a.created_at), [runs]);
  const run = selected ? runs[selected] : undefined;

  return (
    <div className="app">
      <Header metrics={metrics} connected={connected} />
      <main className="layout">
        <aside className="sidebar">
          <NewRun onCreated={(ids) => select(ids.length === 1 ? ids[0] : null)} />
          <div className="card list-card">
            <h2>Runs <span className="muted small">{list.length}</span></h2>
            <RunList runs={list} selected={selected} onSelect={select} now={now} />
          </div>
        </aside>
        <div className="content">
          {run ? <RunDetail key={run.id} run={run} now={now} onBack={() => select(null)} /> : <Fleet runs={list} onSelect={select} />}
        </div>
      </main>
    </div>
  );
}
