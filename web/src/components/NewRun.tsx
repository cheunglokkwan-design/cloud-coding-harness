import { useState } from "react";
import { createRun } from "../lib/api";

const DEFAULT_TASK =
  "median() returns the wrong value for even-length lists and crashes with IndexError on empty input " +
  "(it should raise ValueError). Fix it.";

export function NewRun({ onCreated }: { onCreated: (ids: string[]) => void }) {
  const [repo, setRepo] = useState("examples/demo-calc");
  const [task, setTask] = useState(DEFAULT_TASK);
  const [provider, setProvider] = useState<"scripted" | "anthropic">("scripted");
  const [count, setCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const ids = await createRun({ repo, task, agent: { provider } }, count);
      onCreated(ids);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card new-run" onSubmit={submit}>
      <h2>New run</h2>
      <label>
        Repository
        <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/org/repo or a path" required />
      </label>
      <label>
        Task
        <textarea value={task} onChange={(e) => setTask(e.target.value)} rows={3} required />
      </label>
      <div className="row">
        <label>
          Agent
          <select value={provider} onChange={(e) => setProvider(e.target.value as any)}>
            <option value="scripted">Scripted (no API key)</option>
            <option value="anthropic">Claude</option>
          </select>
        </label>
        <label>
          Agents
          <input type="number" min={1} max={500} value={count} onChange={(e) => setCount(Number(e.target.value))} />
        </label>
        <button type="submit" disabled={busy}>{busy ? "Starting…" : count > 1 ? `Launch ${count}` : "Launch"}</button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}
