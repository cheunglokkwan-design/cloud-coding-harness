from harness.repocache import RepoCache


async def test_snapshot_dir_and_mirror_hit(tmp_path, demo_repo):
    cache = RepoCache(tmp_path / "mirrors")
    a = await cache.checkout(demo_repo, None, tmp_path / "w1", "harness/a")
    assert not a.mirror_hit
    assert (a.path / "calc" / "stats.py").exists()
    b = await cache.checkout(demo_repo, None, tmp_path / "w2", "harness/b")
    assert b.mirror_hit and b.commit == a.commit
    # --shared clone: objects come from the mirror via alternates, not copied.
    assert (b.path / ".git" / "objects" / "info" / "alternates").exists()


async def test_git_repo_source_and_ref(tmp_path, tiny_repo):
    cache = RepoCache(tmp_path / "mirrors")
    r = await cache.checkout(str(tiny_repo), "main", tmp_path / "w", "harness/x")
    assert (r.path / "hello.txt").read_text() == "hello\n"
    assert cache.misses == 1
