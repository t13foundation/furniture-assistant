from __future__ import annotations

import os
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
    old_archive = old_project / "modules" / "history-archive-old"
    recent_archive = old_project / "modules" / "history-archive-recent"
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
