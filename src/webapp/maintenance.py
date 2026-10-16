from __future__ import annotations

import argparse
import os
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import AppConfig
from dotenv import load_dotenv

from src.webapp.store import ProjectNotFoundError, ProjectStore

REPO_ROOT = Path(__file__).resolve().parents[2]


ARCHIVE_TIMESTAMP = re.compile(r"history-archive-(\d{8}_\d{6})$")
TRASH_TIMESTAMP = re.compile(r".+-(\d{8}_\d{6}_\d{6})$")


class MaintenanceRootError(RuntimeError):
    """Raised when maintenance is pointed at a missing or unsafe projects root."""


def _projects_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise MaintenanceRootError(f"Projects root does not exist or is not a real directory: {root}")
    return root.resolve()


def _confined(path: Path, base: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        path.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        return False
    return True


def _timestamp(value: str, format: str) -> float | None:
    try:
        return datetime.strptime(value, format).timestamp()
    except ValueError:
        return None


def projects_root() -> Path:
    load_dotenv(REPO_ROOT / ".env")
    config = AppConfig.from_yaml(REPO_ROOT / "config" / "default.yaml")
    output_dir = Path(config.output.dir)
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir
    return output_dir / "webapp" / "projects"


def _tree_bytes(path: Path) -> int:
    return sum(item.lstat().st_size for item in path.rglob("*") if not item.is_symlink() and item.is_file())


def _modified_at(path: Path, *, ignore_pages: bool = False) -> float:
    """Newest non-cache file timestamp, so cache access never makes a project active."""
    newest = 0.0
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if relative.parts[0] == ".lock" or any(part.startswith("history-archive-") for part in relative.parts):
            continue
        if ignore_pages and "pages" in relative.parts:
            continue
        if item.is_symlink() or not item.is_file():
            continue
        try:
            newest = max(newest, item.stat(follow_symlinks=False).st_mtime)
        except FileNotFoundError:
            continue
    return newest or path.stat(follow_symlinks=False).st_mtime


def prune(root: Path, older_than_days: int, *, archives: bool, pages: bool, trash: bool, dry_run: bool) -> int:
    root = _projects_root(root)
    cutoff = (datetime.now() - timedelta(days=older_than_days)).timestamp()
    store = ProjectStore(root)
    removed_bytes = 0

    def remove(path: Path, description: str, size: int, base: Path) -> None:
        nonlocal removed_bytes
        if not _confined(path, base):
            print(f"Skipping unsafe symlink or out-of-root target: {path}")
            return
        removed_bytes += size
        print(f"{'Would remove' if dry_run else 'Removing'} {description}: {path} ({size} bytes)")
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)

    def prune_project(project_dir: Path) -> None:
        if archives:
            modules = project_dir / "modules"
            if _confined(modules, root) and modules.is_dir():
                for archive in sorted(modules.glob("history-archive-*")):
                    if archive.is_symlink() or not archive.is_dir() or not _confined(archive, root):
                        continue
                    match = ARCHIVE_TIMESTAMP.fullmatch(archive.name)
                    archived_at = _timestamp(match.group(1), "%Y%m%d_%H%M%S") if match else None
                    if archived_at is not None and archived_at < cutoff:
                        remove(archive, "archive", _tree_bytes(archive), root)
        if pages and _modified_at(project_dir, ignore_pages=True) < cutoff:
            cache = project_dir / "pages"
            if _confined(cache, root) and cache.is_dir():
                for page in sorted(cache.glob("*.png")):
                    if not page.is_symlink() and page.is_file() and _confined(page, root):
                        remove(page, "page cache", page.stat(follow_symlinks=False).st_size, root)

    for project_dir in sorted(root.iterdir()):
        if project_dir.is_symlink() or not project_dir.is_dir() or not (project_dir / "project.json").is_file():
            continue
        try:
            with store.lock(project_dir.name):
                prune_project(project_dir)
        except ProjectNotFoundError:
            # Deleted (moved to the trash) by the app between listing and locking.
            print(f"Skipping vanished project during prune: {project_dir}")

    if trash:
        trash_dir = root.parent / "trash"
        if not trash_dir.is_symlink() and trash_dir.is_dir():
            trash_root = trash_dir.resolve()
            trash_store = ProjectStore(trash_root)
            for item in sorted(trash_root.iterdir()):
                if item.is_symlink() or not item.is_dir() or not _confined(item, trash_root):
                    continue
                match = TRASH_TIMESTAMP.fullmatch(item.name)
                trashed_at = _timestamp(match.group(1), "%Y%m%d_%H%M%S_%f") if match else None
                if trashed_at is None:
                    trashed_at = item.stat(follow_symlinks=False).st_mtime
                if trashed_at < cutoff:
                    with trash_store.lock(item.name):
                        remove(item, "trashed project", _tree_bytes(item), trash_root)
    print(f"{'Would free' if dry_run else 'Freed'} {removed_bytes} bytes")
    return removed_bytes


def backup(root: Path, output: Path, *, include_pages: bool, allow_empty: bool = False) -> None:
    root = _projects_root(root)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    trash_dir = root.parent / "trash"
    with tempfile.TemporaryDirectory(prefix="furniture-assistant-backup-") as temp:
        snapshot = Path(temp) / "snapshot"
        snapshot.mkdir()
        count = 0
        skipped = 0

        def copy_projects(source: Path, destination_name: str) -> None:
            nonlocal count, skipped
            if source.is_symlink() or not source.is_dir():
                return
            source_real = source.resolve()
            source_store = ProjectStore(source_real)
            destination = snapshot / destination_name
            destination.mkdir()
            for project_dir in sorted(source_real.iterdir()):
                if project_dir.is_symlink() or not project_dir.is_dir() or not (project_dir / "project.json").is_file():
                    continue
                if not _confined(project_dir, source_real):
                    continue

                try:
                    with source_store.lock(project_dir.name):
                        if (
                            project_dir.is_symlink()
                            or not project_dir.is_dir()
                            or not (project_dir / "project.json").is_file()
                        ):
                            skipped += 1
                            print(f"Skipping vanished project during backup: {project_dir}")
                            continue
                        ignored = {".lock", ".maintenance.lock"}
                        if not include_pages:
                            ignored.add("pages")
                        shutil.copytree(
                            project_dir,
                            destination / project_dir.name,
                            symlinks=True,
                            ignore=lambda _directory, names, ignored=ignored: [
                                name for name in names if name in ignored
                            ],
                        )
                except (FileNotFoundError, ProjectNotFoundError):
                    if project_dir.is_symlink() or not project_dir.is_dir():
                        skipped += 1
                        print(f"Skipping vanished project during backup: {project_dir}")
                        continue
                    raise
                count += 1

        copy_projects(root, "projects")
        copy_projects(trash_dir, "trash")
        if count == 0 and output.exists() and not allow_empty:
            raise MaintenanceRootError(
                f"Refusing to replace existing backup with an empty snapshot: {output} (pass --allow-empty to override)"
            )
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
        os.close(descriptor)
        temporary_output = Path(temporary_name)
        try:
            with tarfile.open(temporary_output, "w:gz") as archive:
                for entry in sorted(snapshot.iterdir()):
                    archive.add(entry, arcname=entry.name)
            os.replace(temporary_output, output)
        finally:
            temporary_output.unlink(missing_ok=True)
    print(
        f"Created backup {output}: {count} projects, skipped {skipped} vanished entries, {output.stat().st_size} bytes"
    )


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
    backup_parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="allow replacing an existing archive when no projects were found",
    )
    args = parser.parse_args()
    try:
        root = _projects_root(projects_root())
    except MaintenanceRootError as exc:
        parser.error(str(exc))
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
        backup(root, args.output, include_pages=args.include_pages, allow_empty=args.allow_empty)


if __name__ == "__main__":
    main()
