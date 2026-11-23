#!/usr/bin/env python3
"""Import an upstream Linux tarball into a case-insensitive macOS-safe tree.

The importer never materializes upstream case-colliding paths. Renames are read
from the kernel repository's casefold-path map and applied while streaming data
out of the archive.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import shutil
import sys
import tarfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


CUSTOM_PATHS = (
    "Documentation/macos-host-portability",
    "scripts/check_casefold_paths.py",
)
CUSTOM_GITIGNORE = (".cache/", "__pycache__/")


def collision_key(path: str) -> str:
    return unicodedata.normalize("NFD", path.rstrip("/")).casefold()


def load_rename_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        for row in reader:
            if row["action"] == "rename":
                result[row["logical_path"]] = row["physical_path"]
    return result


def strip_root(name: str, root: str) -> str:
    if name == root or name == root + "/":
        return ""
    prefix = root + "/"
    if not name.startswith(prefix):
        raise ValueError(f"archive entry outside root {root!r}: {name!r}")
    return name[len(prefix):].rstrip("/")


def safe_relative(path: str) -> Path:
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe archive path: {path!r}")
    return rel


def derive_replacements(rename_map: dict[str, str]) -> list[tuple[bytes, bytes]]:
    replacements: set[tuple[bytes, bytes]] = set()
    for logical, physical in rename_map.items():
        logical_path = Path(logical)
        physical_path = Path(physical)

        if logical.startswith("include/uapi/") and physical.startswith("include/uapi/"):
            old = logical.removeprefix("include/uapi/")
            new = physical.removeprefix("include/uapi/")
            replacements.add((old.encode(), new.encode()))

        if logical_path.suffix == ".c" and physical_path.suffix == ".c":
            old_object = logical_path.with_suffix(".o").name
            new_object = physical_path.with_suffix(".o").name
            replacements.add((old_object.encode(), new_object.encode()))

        if logical_path.suffix == ".litmus":
            replacements.add((logical_path.name.encode(), physical_path.name.encode()))

    return sorted(replacements, key=lambda pair: len(pair[0]), reverse=True)


def transform_data(data: bytes, replacements: list[tuple[bytes, bytes]]) -> tuple[bytes, int]:
    if b"\0" in data:
        return data, 0

    changed = 0
    for old, new in replacements:
        count = data.count(old)
        if count:
            data = data.replace(old, new)
            changed += count
    return data, changed


def archive_root(archive: tarfile.TarFile) -> str:
    roots: set[str] = set()
    for member in archive:
        cleaned = member.name.strip("/")
        if cleaned:
            roots.add(cleaned.split("/", 1)[0])
        if len(roots) > 1:
            raise ValueError(f"archive has multiple roots: {sorted(roots)!r}")
    if len(roots) != 1:
        raise ValueError("archive root could not be determined")
    return next(iter(roots))


def preflight(
    archive_path: Path,
    rename_map: dict[str, str],
) -> tuple[str, Counter[str], int]:
    with tarfile.open(archive_path, "r:*") as archive:
        root = archive_root(archive)

    groups: dict[str, list[str]] = defaultdict(list)
    types: Counter[str] = Counter()
    entries = 0

    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            rel = strip_root(member.name, root)
            if not rel:
                continue
            safe_relative(rel)
            physical = rename_map.get(rel, rel)
            safe_relative(physical)
            groups[collision_key(physical)].append(physical)
            entries += 1

            if member.isdir():
                types["dir"] += 1
            elif member.isfile():
                types["file"] += 1
            elif member.issym():
                types["symlink"] += 1
            elif member.islnk():
                types["hardlink"] += 1
            else:
                types["unsupported"] += 1

    collisions = [
        sorted(set(paths))
        for paths in groups.values()
        if len(set(paths)) > 1
    ]
    if collisions:
        raise ValueError(f"mapped tree still has case-fold collisions: {collisions!r}")
    if types["unsupported"]:
        raise ValueError(f"archive contains unsupported entry types: {types!r}")

    physical_paths = {path for paths in groups.values() for path in paths}
    for custom in CUSTOM_PATHS:
        if custom in physical_paths:
            raise ValueError(f"upstream archive conflicts with custom path: {custom}")

    return root, types, entries


def replace_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists() and not path.is_dir():
        raise ValueError(f"unsupported existing destination: {path}")


def mapped_link_target(linkname: str, root: str, rename_map: dict[str, str]) -> str:
    if linkname.startswith(root + "/"):
        rel = strip_root(linkname, root)
        return rename_map.get(rel, rel)
    return linkname


def import_tree(
    archive_path: Path,
    destination: Path,
    root: str,
    rename_map: dict[str, str],
) -> tuple[int, int]:
    replacements = derive_replacements(rename_map)
    hardlinks: list[tuple[Path, str, int]] = []
    imported = 0
    content_replacements = 0

    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            rel = strip_root(member.name, root)
            if not rel:
                continue

            physical = rename_map.get(rel, rel)
            target = destination / safe_relative(physical)
            target.parent.mkdir(parents=True, exist_ok=True)

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, member.mode & 0o7777)
            elif member.isfile():
                replace_existing(target)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"could not read regular file: {member.name}")
                data, count = transform_data(source.read(), replacements)
                target.write_bytes(data)
                os.chmod(target, member.mode & 0o7777)
                content_replacements += count
            elif member.issym():
                replace_existing(target)
                os.symlink(
                    mapped_link_target(member.linkname, root, rename_map),
                    target,
                )
            elif member.islnk():
                hardlinks.append((target, member.linkname, member.mode))
            else:
                raise ValueError(f"unsupported archive member: {member.name}")

            imported += 1

    for target, linkname, mode in hardlinks:
        replace_existing(target)
        mapped = mapped_link_target(linkname, root, rename_map)
        source = destination / safe_relative(mapped)
        if not source.exists():
            raise ValueError(f"hardlink target does not exist: {mapped}")
        os.link(source, target)
        os.chmod(target, mode & 0o7777)

    gitignore = destination / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    additions = "".join(
        f"{entry}\n" for entry in CUSTOM_GITIGNORE if entry not in existing.splitlines()
    )
    if additions:
        gitignore.write_text(existing + additions, encoding="utf-8")

    return imported, content_replacements


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_tree(
    archive_path: Path,
    destination: Path,
    root: str,
    rename_map: dict[str, str],
) -> tuple[int, int]:
    replacements = derive_replacements(rename_map)
    checked = 0
    mismatches = 0

    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive:
            rel = strip_root(member.name, root)
            if not rel or member.isdir():
                continue

            physical = rename_map.get(rel, rel)
            target = destination / safe_relative(physical)

            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    print(f"VERIFY unreadable: {rel}", file=sys.stderr)
                    mismatches += 1
                    continue
                expected, _ = transform_data(source.read(), replacements)
                try:
                    actual = target.read_bytes()
                except OSError:
                    print(f"VERIFY missing: {physical}", file=sys.stderr)
                    mismatches += 1
                    continue
                if actual != expected:
                    if physical == ".gitignore":
                        if not actual.startswith(expected):
                            print(f"VERIFY content mismatch: {physical}", file=sys.stderr)
                            mismatches += 1
                    else:
                        print(f"VERIFY content mismatch: {physical}", file=sys.stderr)
                        mismatches += 1
            elif member.issym():
                if not target.is_symlink():
                    print(f"VERIFY symlink missing: {physical}", file=sys.stderr)
                    mismatches += 1
                else:
                    expected = mapped_link_target(member.linkname, root, rename_map)
                    if os.readlink(target) != expected:
                        print(f"VERIFY symlink mismatch: {physical}", file=sys.stderr)
                        mismatches += 1
            elif member.islnk():
                if not target.exists():
                    print(f"VERIFY hardlink missing: {physical}", file=sys.stderr)
                    mismatches += 1

            checked += 1

    return checked, mismatches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    archive_path = args.archive.resolve()
    destination = args.destination.resolve()
    map_path = args.map_path.resolve()

    if args.expected_sha256:
        actual_sha = sha256(archive_path)
        if actual_sha != args.expected_sha256:
            print(
                f"archive SHA-256 mismatch: expected={args.expected_sha256} actual={actual_sha}",
                file=sys.stderr,
            )
            return 2
        print(f"archive_sha256={actual_sha}")

    rename_map = load_rename_map(map_path)
    root, types, entries = preflight(archive_path, rename_map)
    print(
        f"preflight root={root} entries={entries} renames={len(rename_map)} "
        f"types={dict(types)} collision_groups=0"
    )

    if args.preflight_only:
        return 0

    if args.verify_only:
        checked, mismatches = verify_tree(
            archive_path,
            destination,
            root,
            rename_map,
        )
        print(f"verify checked={checked} mismatches={mismatches}")
        return 1 if mismatches else 0

    imported, content_replacements = import_tree(
        archive_path,
        destination,
        root,
        rename_map,
    )
    print(
        f"imported={imported} path_renames={len(rename_map)} "
        f"content_replacements={content_replacements}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
