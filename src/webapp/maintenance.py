from __future__ import annotations

import argparse
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import AppConfig
from dotenv import load_dotenv

from src.webapp.store import ProjectStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def projects_root() -> Path:
    load_dotenv(REPO_ROOT / ".env")
    config = AppConfig.from_yaml(REPO_ROOT / "config" / "default.yaml")
    output_dir = Path(config.output.dir)
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    return output_dir / "webapp" / "projects"


def _tree_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _modified_at(path: Path, *, ignore_pages: bool = False) -> float:
    """Newest non-cache file timestamp, so cache access never makes a project active."""
    newest = 0.0
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if relative.parts[0] == ".lock" or any(part.startswith("history-archive-") for part in relative.parts):
            continue
        if ignore_pages and "pages" in relative.parts:
            continue
        if not item.is_file():
            continue
        try:
            newest = max(newest, item.stat().st_mtime)
        except FileNotFoundError:
            continue
    return newest or path.stat().st_mtime


def prune(root: Path, older_than_days: int, *, archives: bool, pages: bool, trash: bool, dry_run: bool) -> int:
    cutoff = (datetime.now() - timedelta(days=older_than_days)).timestamp()
    store = ProjectStore(root)
    removed_bytes = 0

    def remove(path: Path, description: str, size: int) -> None:
        nonlocal removed_bytes
        removed_bytes += size
        print(f"{'Would remove' if dry_run else 'Removing'} {description}: {path} ({size} bytes)")
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    if root.is_dir():
        for project_dir in sorted(root.iterdir()):
            if not project_dir.is_dir() or not (project_dir / "project.json").is_file():
                continue
            with store.lock(project_dir.name):
                if archives:
                    modules = project_dir / "modules"
                    for archive in sorted(modules.glob("history-archive-*")) if modules.is_dir() else ():
                        if archive.is_dir() and archive.stat().st_mtime < cutoff:
                            remove(archive, "archive", _tree_bytes(archive))
                if pages and _modified_at(project_dir, ignore_pages=True) < cutoff:
                    cache = project_dir / "pages"
                    if cache.is_dir():
                        for page in sorted(cache.glob("*.png")):
                            if page.is_file():
                                remove(page, "page cache", page.stat().st_size)

        if trash:
            trash_dir = root / "trash"
            if trash_dir.is_dir():
                trash_store = ProjectStore(trash_dir)
                for item in sorted(trash_dir.iterdir()):
                    if item.is_dir() and _modified_at(item) < cutoff:
                        with trash_store.lock(item.name):
                            remove(item, "trashed project", _tree_bytes(item))
    print(f"{'Would free' if dry_run else 'Freed'} {removed_bytes} bytes")
    return removed_bytes


def backup(root: Path, output: Path, *, include_pages: bool) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    store = ProjectStore(root)
    with tempfile.TemporaryDirectory(prefix="furniture-assistant-backup-") as temp:
        snapshot = Path(temp) / "projects"
        snapshot.mkdir()
        count = 0
        if root.is_dir():
            for project_dir in sorted(root.iterdir()):
                if not project_dir.is_dir() or not (project_dir / "project.json").is_file():
                    continue
                with store.lock(project_dir.name):
                    ignored = {".lock", ".maintenance.lock"}
                    if not include_pages:
                        ignored.add("pages")
                    shutil.copytree(
                        project_dir,
                        snapshot / project_dir.name,
                        ignore=lambda _directory, names, ignored=ignored: [name for name in names if name in ignored],
                    )
                count += 1
        with tarfile.open(output, "w:gz") as archive:
            for project_dir in sorted(snapshot.iterdir()):
                archive.add(project_dir, arcname=project_dir.name)
    print(f"Created backup {output}: {count} projects, {output.stat().st_size} bytes")


def _nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from None
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and prune Furniture Assistant web projects")
    commands = parser.add_subparsers(dest="command", required=True)
    prune_parser = commands.add_parser("prune", help="remove old archives, page caches and trashed projects")
    prune_parser.add_argument("--older-than-days", type=_nonnegative_int, required=True)
    prune_parser.add_argument("--archives", action="store_true", help="remove old module history archives")
    prune_parser.add_argument("--pages", action="store_true", help="remove page caches from inactive projects")
    prune_parser.add_argument("--trash", action="store_true", help="remove old projects from the trash directory")
    prune_parser.add_argument("--dry-run", action="store_true", help="report removals without deleting")
    backup_parser = commands.add_parser("backup", help="create a consistent compressed project backup")
    backup_parser.add_argument("--output", type=Path, required=True)
    backup_parser.add_argument("--include-pages", action="store_true", help="include regenerable page PNG caches")
    args = parser.parse_args()
    root = projects_root()
    if args.command == "prune":
        if not any((args.archives, args.pages, args.trash)):
            parser.error("prune requires at least one of --archives, --pages or --trash")
        prune(
            root,
            args.older_than_days,
            archives=args.archives,
            pages=args.pages,
            trash=args.trash,
            dry_run=args.dry_run,
        )
    else:
        backup(root, args.output, include_pages=args.include_pages)


if __name__ == "__main__":
    main()
