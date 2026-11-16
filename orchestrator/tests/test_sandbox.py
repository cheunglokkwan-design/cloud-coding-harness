import pytest

from harness.sandbox import LocalProvider, PathError, safe_relpath


@pytest.mark.parametrize("path,expected", [
    ("a/b.py", "a/b.py"), ("./a//b.py", "a/b.py"), ("/workspace/x.py", "x.py"), ("a/../b", "b"), (".", "."),
])
def test_safe_relpath_accepts(path, expected):
    assert safe_relpath(path) == expected


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "a/../../x", "/workspace/../etc"])
def test_safe_relpath_rejects(path):
    with pytest.raises(PathError):
        safe_relpath(path)


async def test_local_exec_read_write_and_timeout(tmp_path):
    p = LocalProvider(tmp_path / "ws")
    await p.start()
    sb = await p.create()
    await sb.write_file("dir/a.txt", "hi there\n")
    assert await sb.read_file("dir/a.txt") == "hi there\n"

    chunks = []

    async def on_output(stream, chunk):
        chunks.append((stream, chunk))

    r = await sb.exec("echo out; echo err >&2; exit 3", on_output=on_output)
    assert r.exit_code == 3 and r.stdout.strip() == "out" and r.stderr.strip() == "err"
    assert {s for s, _ in chunks} == {"stdout", "stderr"}

    r = await sb.exec("sleep 5", timeout=0.3)
    assert r.timed_out and not r.ok
    await sb.destroy()
    assert not sb.host_path.exists()


async def test_output_is_capped(tmp_path):
    p = LocalProvider(tmp_path / "ws", max_output=1000)
    sb = await p.create()
    r = await sb.exec("yes x | head -c 50000")
    assert len(r.stdout) < 1200 and "truncated" in r.stdout
