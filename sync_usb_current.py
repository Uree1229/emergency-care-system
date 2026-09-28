"""Copy current project deliverables to the existing USB folder without deleting files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
TARGET = Path("D:/Capstone-20260923")
ROOT_SUFFIXES = {".py", ".md", ".docx", ".pdf", ".zip", ".json", ".cmd"}
SUBDIRS = ("assets", "optimization_results", "optimization_results_v2", "prototype")
SKIP_DIRS = {"__pycache__", "_runtime", ".solver_deps", ".git", ".usb_export"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files() -> list[Path]:
    files = [p for p in SOURCE.iterdir() if p.is_file() and p.suffix.lower() in ROOT_SUFFIXES]
    for name in SUBDIRS:
        folder = SOURCE / name
        if folder.is_dir():
            files.extend(
                p for p in folder.rglob("*")
                if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(SOURCE).parts)
                and p.suffix.lower() != ".pyc"
            )
    return sorted(files)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="copy files and refresh manifest")
    args = parser.parse_args()

    target = TARGET.resolve(strict=True)
    if target.drive.upper() != "D:" or target.name != "Capstone-20260923":
        raise RuntimeError(f"Unexpected USB destination: {target}")
    if not target.is_dir():
        raise RuntimeError("USB project directory does not exist")

    changed: list[tuple[Path, Path]] = []
    unchanged = 0
    for source in source_files():
        relative = source.relative_to(SOURCE)
        if relative.name == "TRANSFER_MANIFEST.json":
            continue
        destination = target / relative
        if destination.is_file() and source.stat().st_size == destination.stat().st_size and sha256(source) == sha256(destination):
            unchanged += 1
        else:
            changed.append((source, destination))

    print(f"destination={target}")
    print(f"changed={len(changed)} unchanged={unchanged} bytes_to_copy={sum(s.stat().st_size for s, _ in changed)}")
    for source, _ in changed:
        print(source.relative_to(SOURCE).as_posix())

    if not args.apply:
        return

    for source, destination in changed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256(source) != sha256(destination):
            raise RuntimeError(f"Copy verification failed: {destination}")

    manifest = []
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name != "TRANSFER_MANIFEST.json" and not any(
            part in SKIP_DIRS for part in path.relative_to(target).parts
        ):
            manifest.append({
                "path": path.relative_to(target).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            })
    manifest_path = target / "TRANSFER_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"verified={len(changed)} manifest_files={len(manifest)}")


if __name__ == "__main__":
    main()
