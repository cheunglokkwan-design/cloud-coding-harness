from harness.agent.loop import AgentLoop
from harness.llm.scripted import ScriptedLLM
from harness.models import ScriptedStep
from harness.repocache import RepoCache
from harness.sandbox import LocalProvider

FIX = {
    "path": "calc/stats.py",
    "old_str": "    s = sorted(values)\n    n = len(s)\n    return s[n // 2]\n",
    "new_str": "    if not values:\n        raise ValueError('empty')\n    s = sorted(values)\n    n = len(s)\n"
               "    m = n // 2\n    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2\n",
}


async def _sandbox(tmp_path, repo):
    p = LocalProvider(tmp_path / "ws")
    sb = await p.create()
    await RepoCache(tmp_path / "mirrors").checkout(repo, None, sb.host_path, "harness/t")
    await p.adopt_workspace(sb)
    return sb


async def test_loop_fixes_bug_and_delivers_patch(tmp_path, demo_repo):
    sb = await _sandbox(tmp_path, demo_repo)
    events = []

    async def emit(type_, phase=None, **data):
        events.append((type_, phase, data))

    llm = ScriptedLLM([
        ScriptedStep(thinking="look", tool_calls=[{"name": "read_file", "input": {"path": "calc/stats.py"}}]),
        ScriptedStep(tool_calls=[{"name": "edit_file", "input": FIX}]),
        ScriptedStep(tool_calls=[{"name": "finish", "input": {"summary": "fixed median"}}]),
    ])
    loop = AgentLoop(sandbox=sb, llm=llm, task="Fix median", emit=emit, artifacts_dir=tmp_path / "art")
    res = await loop.run()
    assert res.tests_passed is True
    assert res.files_changed == 1
    patch = res.patch_path.read_text()
    assert "Subject: [PATCH] Fix median" in patch and "+    if not values:" in patch
    types = [t for t, _, _ in events]
    for expected in ("analysis", "test.result", "tool.call", "tool.result", "diff", "delivery", "terminal"):
        assert expected in types
    baseline = next(d for t, _, d in events if t == "test.result")
    assert baseline["baseline"] and not baseline["passed"]


async def test_loop_repairs_after_failing_tests(tmp_path, demo_repo):
    sb = await _sandbox(tmp_path, demo_repo)

    async def emit(*a, **k):
        pass

    # First "finish" happens without a fix; the harness must feed the failure back.
    llm = ScriptedLLM([
        ScriptedStep(tool_calls=[{"name": "finish", "input": {"summary": "nothing"}}]),
        ScriptedStep(tool_calls=[{"name": "edit_file", "input": FIX}]),
        ScriptedStep(tool_calls=[{"name": "finish", "input": {"summary": "fixed after feedback"}}]),
    ])
    loop = AgentLoop(sandbox=sb, llm=llm, task="Fix median", emit=emit, artifacts_dir=tmp_path / "art")
    res = await loop.run()
    assert res.tests_passed is True and res.summary == "fixed after feedback"


async def test_tool_errors_are_reported_not_raised(tmp_path, demo_repo):
    sb = await _sandbox(tmp_path, demo_repo)
    from harness.agent.tools import ToolExecutor

    async def started(cmd):
        async def cb(s, c):
            pass
        return "c1", cb

    async def done(*a):
        pass

    t = ToolExecutor(sb, started, done)
    assert (await t.call("read_file", {"path": "../../etc/passwd"})).is_error
    assert (await t.call("read_file", {"path": "missing.py"})).is_error
    out = await t.call("edit_file", {"path": "calc/stats.py", "old_str": "nope", "new_str": "x"})
    assert out.is_error and "found 0 times" in out.output
    assert (await t.call("bogus", {})).is_error
