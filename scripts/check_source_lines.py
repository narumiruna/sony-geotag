from __future__ import annotations

import argparse
from pathlib import Path

MAX_LINES = 1000
DEFAULT_ROOTS = (Path("src"), Path("tests"), Path("scripts"))


def source_files(roots: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.add(root)
            continue
        if not root.exists():
            continue
        files.update(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix == ".py"
            and "build" not in path.parts
            and ".git" not in path.parts
        )
    return sorted(files)


def line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as source:
        return sum(1 for _line in source)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject Python program sources over 1000 lines.")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    files = source_files(args.paths or list(DEFAULT_ROOTS))
    failures = [(path, count) for path in files if (count := line_count(path)) > MAX_LINES]
    if failures:
        for path, count in failures:
            print(f"{path}: {count} lines exceeds {MAX_LINES}")
        return 1
    print(f"Source line check passed: {len(files)} file(s), maximum {MAX_LINES} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
