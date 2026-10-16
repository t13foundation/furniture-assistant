from __future__ import annotations

import os
import shutil
import tarfile
import time
from pathlib import Path

from src.webapp.maintenance import backup, prune
from src.webapp.store import ProjectStore


def _make_project(root: Path, name: str) -> Path:
    project = ProjectStore(root).create_project(name, [("drawing.png", b"drawing")])
    return root / project.id


def _set_old(path: Path) -> None:
    old = time.time() - 3 * 24 * 60 * 60
    path.touch()
    os.utime(path, (old, old))


def test_prune_removes_only_eligible_archives_and_inactive_pages(tmp_path, capsys):
    root = tmp_path / "projects"
    root.mkdir()
    old_project = _make_project(root, "Old project")
    active_project = _make_project(root, "Active project")
    old_pages = old_project / "pages"
    active_pages = active_project / "pages"
    old_pages.mkdir()
    active_pages.mkdir()
    (old_pages / "0_0.png").write_bytes(b"old cache")
    (active_pages / "0_0.png").write_bytes(b"active cache")
    old_archive = old_project / "modules" / "history-archive-20200101_000000"
    recent_archive = old_project / "modules" / f"history-archive-{time.strftime('%Y%m%d_%H%M%S')}"
    old_archive.mkdir(parents=True)
    recent_archive.mkdir()
    (old_archive / "old.json").write_text("old")
    (recent_archive / "recent.json").write_text("recent")
    _set_old(old_project / "project.json")
    _set_old(old_project / "uploads" / "drawing.png")
    _set_old(old_archive)
    _set_old(old_archive / "old.json")

    prune(root, 1, archives=True, pages=True, trash=False, dry_run=True)
    output = capsys.readouterr().out
    assert "Would remove archive" in output
    assert "Would remove page cache" in output
    assert old_archive.exists()
    assert (old_pages / "0_0.png").exists()

    prune(root, 1, archives=True, pages=True, trash=False, dry_run=False)
    assert not old_archive.exists()
    assert recent_archive.exists()
    assert not (old_pages / "0_0.png").exists()
    assert (active_pages / "0_0.png").exists()
    assert (old_project / "project.json").exists()
    assert (old_project / "uploads" / "drawing.png").exists()


def test_backup_contains_durable_project_files_and_omits_pages_by_default(tmp_path, capsys):
    root = tmp_path / "projects"
    root.mkdir()
    project_dir = _make_project(root, "Backup project")
    (project_dir / "modules").mkdir()
    (project_dir / "modules" / "cabinet.json").write_text('{"module":"data"}')
    (project_dir / "audit.jsonl").write_text('{"action":"created"}\n')
    (project_dir / "pages").mkdir()
    (project_dir / "pages" / "0_0.png").write_bytes(b"rendered image")
    output = tmp_path / "backup.tar.gz"

    backup(root, output, include_pages=False)

    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
        assert any(name.endswith("/project.json") for name in names)
        assert any(name.endswith("/modules/cabinet.json") for name in names)
        assert any(name.endswith("/uploads/drawing.png") for name in names)
        assert any(name.endswith("/audit.jsonl") for name in names)
        assert not any("/pages/" in name for name in names)
    assert "1 projects" in capsys.readouterr().out


def test_archive_pruning_uses_timestamp_in_name_not_mtime(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    project = _make_project(root, "Archive dates")
    modules = project / "modules"
    modules.mkdir()
    old_archive = modules / "history-archive-20200101_000000"
    recent_archive = modules / "history-archive-20990101_000000"
    old_archive.mkdir()
    recent_archive.mkdir()
    (old_archive / "old.json").write_text("old")
    (recent_archive / "recent.json").write_text("recent")
    now = time.time()
    os.utime(old_archive, (now, now))
    old = now - 10 * 24 * 60 * 60
    os.utime(recent_archive, (old, old))

    prune(root, 1, archives=True, pages=False, trash=False, dry_run=False)

    assert not old_archive.exists()
    assert recent_archive.exists()


def test_trash_pruning_uses_trashed_at_suffix_and_parent_trash_location(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    trash = root.parent / "trash"
    trash.mkdir()
    old_project = trash / "old-20200101_000000_000000"
    recent_project = trash / "recent-20990101_000000_000000"
    old_project.mkdir()
    recent_project.mkdir()
    (old_project / "project.json").write_text("old project")
    (recent_project / "project.json").write_text("recent project")
    old = time.time() - 100 * 24 * 60 * 60
    os.utime(old_project / "project.json", (old, old))
    os.utime(recent_project / "project.json", (old, old))

    prune(root, 1, archives=False, pages=False, trash=True, dry_run=False)

    assert not old_project.exists()
    assert recent_project.exists()


def test_trash_pruning_falls_back_to_directory_mtime(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    trash = root.parent / "trash"
    trash.mkdir()
    item = trash / "legacy-name"
    item.mkdir()
    old = time.time() - 3 * 24 * 60 * 60
    os.utime(item, (old, old))

    prune(root, 1, archives=False, pages=False, trash=True, dry_run=False)

    assert not item.exists()


def test_prune_rejects_symlinked_archive_target(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    project = _make_project(root, "Symlink")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "important.json").write_text("keep")
    modules = project / "modules"
    modules.mkdir()
    link = modules / "history-archive-20200101_000000"
    link.symlink_to(outside, target_is_directory=True)
    old = time.time() - 3 * 24 * 60 * 60
    os.utime(link, (old, old))

    prune(root, 1, archives=True, pages=False, trash=False, dry_run=False)

    assert (outside / "important.json").exists()


def test_backup_includes_parent_trash_and_does_not_follow_symlinks(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    project = _make_project(root, "Backup project")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not included")
    (project / "external-link").symlink_to(outside, target_is_directory=True)
    trashed = root.parent / "trash" / "deleted-20261015_120000_123456"
    trashed.mkdir(parents=True)
    (trashed / "project.json").write_text("trashed")
    output = tmp_path / "backup.tar.gz"

    backup(root, output, include_pages=False)

    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
        assert any(name.startswith("trash/") and name.endswith("/project.json") for name in names)
        assert not any(name.endswith("secret.txt") for name in names)
        link = next(member for member in archive.getmembers() if member.name.endswith("/external-link"))
        assert link.issym()


def test_backup_failure_preserves_previous_output(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    _make_project(root, "Backup project")
    output = tmp_path / "backup.tar.gz"
    output.write_bytes(b"previous backup")
    real_add = tarfile.TarFile.add

    def fail_adding(self, *args, **kwargs):
        raise OSError("archive failed after output was opened")

    monkeypatch.setattr(tarfile.TarFile, "add", fail_adding)
    try:
        backup(root, output, include_pages=False)
    except OSError:
        pass
    else:
        monkeypatch.setattr(tarfile.TarFile, "add", real_add)
        raise AssertionError("backup should propagate archive failures")

    assert output.read_bytes() == b"previous backup"


def test_maintenance_rejects_missing_projects_root(tmp_path, monkeypatch, capsys):
    import sys

    import pytest
    import src.webapp.maintenance as maintenance

    root = tmp_path / "missing"
    with pytest.raises(RuntimeError):
        prune(root, 1, archives=True, pages=False, trash=False, dry_run=True)
    with pytest.raises(RuntimeError):
        backup(root, tmp_path / "backup.tar.gz", include_pages=False)
    monkeypatch.setattr(maintenance, "projects_root", lambda: root)
    monkeypatch.setattr(sys, "argv", ["maintenance", "backup", "--output", str(tmp_path / "backup.tar.gz")])
    with pytest.raises(SystemExit) as error:
        maintenance.main()
    assert error.value.code == 2
    assert "Projects root does not exist" in capsys.readouterr().err


def test_maintenance_allows_fresh_empty_projects_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    assert prune(root, 1, archives=True, pages=True, trash=True, dry_run=True) == 0
    output = tmp_path / "backup.tar.gz"

    backup(root, output, include_pages=False)

    assert output.is_file()
    with tarfile.open(output, "r:gz") as archive:
        assert "projects" in archive.getnames()


def test_backup_refuses_empty_snapshot_over_existing_archive_unless_allowed(tmp_path):
    import pytest

    root = tmp_path / "wrong-root"
    root.mkdir()
    (root / "unrelated.txt").write_text("not a project")
    output = tmp_path / "backup.tar.gz"
    output.write_bytes(b"previous backup")

    with pytest.raises(RuntimeError, match="empty snapshot"):
        backup(root, output, include_pages=False)
    assert output.read_bytes() == b"previous backup"

    backup(root, output, include_pages=False, allow_empty=True)

    with tarfile.open(output, "r:gz") as archive:
        assert "projects" in archive.getnames()


def test_backup_skips_project_vanished_before_lock(tmp_path, monkeypatch, capsys):
    root = tmp_path / "projects"
    root.mkdir()
    project = _make_project(root, "Vanishing project")
    project_id = project.name
    original_lock = ProjectStore.lock

    def vanish_before_lock(store, requested_id):
        if store.root.resolve() == root.resolve() and requested_id == project_id:
            shutil.rmtree(project)
        return original_lock(store, requested_id)

    monkeypatch.setattr(ProjectStore, "lock", vanish_before_lock)
    output = tmp_path / "backup.tar.gz"

    backup(root, output, include_pages=False)

    assert output.is_file()
    assert not project.exists()
    assert "Skipping vanished project during backup" in capsys.readouterr().out
