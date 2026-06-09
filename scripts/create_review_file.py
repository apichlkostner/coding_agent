#!/usr/bin/env python3
"""
review_export.py

Creates a review file from:
  - a source folder
  - a git diff

Examples:

Folder mode:
    python review_export.py folder \
        --path ./src \
        --output review.md

Git diff mode:
    python review_export.py diff \
        --repo . \
        --range HEAD~1..HEAD \
        --output review.md

Uncommitted changes:
    python review_export.py diff \
        --repo . \
        --output review.md
"""

from __future__ import annotations

import argparse
import fnmatch
import pathlib
import subprocess
import sys
from typing import Iterable


DEFAULT_EXTENSIONS = {
    ".py",
    ".java",
    ".kt",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".scala",
    ".sql",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".sh",
    ".dockerfile",
}


DEFAULT_EXCLUDES = {
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".venv",
    "venv",
}


def run_git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def is_text_file(path: pathlib.Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(4096)
        return b"\x00" not in chunk
    except Exception:
        return False


def collect_files(
    root: pathlib.Path,
    extensions: set[str] | None,
    exclude_patterns: set[str],
) -> list[pathlib.Path]:
    files = []

    for path in root.rglob("*"):
        rel = path.relative_to(root)

        if any(part in exclude_patterns for part in rel.parts):
            continue

        if path.is_dir():
            continue

        if extensions:
            if path.suffix.lower() not in extensions:
                continue

        if not is_text_file(path):
            continue

        files.append(path)

    return sorted(files)


def write_file_section(
    out,
    relative_path: str,
    content: str,
) -> None:
    out.write(f"\n# FILE: {relative_path}\n\n")
    out.write("```\n")
    out.write(content)
    if not content.endswith("\n"):
        out.write("\n")
    out.write("```\n")


def export_folder(
    root: pathlib.Path,
    output: pathlib.Path,
    extensions: set[str] | None,
) -> None:
    files = collect_files(root, extensions, DEFAULT_EXCLUDES)

    with output.open("w", encoding="utf-8") as out:
        out.write("# Source Code Review Package\n\n")
        out.write(f"Root: {root}\n\n")
        out.write(f"Files: {len(files)}\n\n")

        for file in files:
            try:
                content = file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file.read_text(
                    encoding="utf-8",
                    errors="replace",
                )

            write_file_section(
                out,
                str(file.relative_to(root)),
                content,
            )


def parse_changed_files(diff_text: str) -> set[str]:
    files = set()

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])

    return files


def export_git_diff(
    repo: pathlib.Path,
    output: pathlib.Path,
    diff_range: str | None,
) -> None:
    if diff_range:
        diff_text = run_git(repo, "diff", diff_range)
    else:
        diff_text = run_git(repo, "diff")

    changed_files = sorted(parse_changed_files(diff_text))

    with output.open("w", encoding="utf-8") as out:
        out.write("# Git Review Package\n\n")

        if diff_range:
            out.write(f"Range: {diff_range}\n\n")
        else:
            out.write("Range: Working tree changes\n\n")

        out.write("## Diff\n\n")
        out.write("```diff\n")
        out.write(diff_text)
        if not diff_text.endswith("\n"):
            out.write("\n")
        out.write("```\n")

        out.write("\n## Changed File Contents\n")

        for rel_path in changed_files:
            file_path = repo / rel_path

            if not file_path.exists():
                continue

            if not file_path.is_file():
                continue

            if not is_text_file(file_path):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )

            write_file_section(
                out,
                rel_path,
                content,
            )


def main() -> int:
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(
        dest="mode",
        required=True,
    )

    folder_parser = subparsers.add_parser("folder")
    folder_parser.add_argument(
        "--path",
        required=True,
        type=pathlib.Path,
    )
    folder_parser.add_argument(
        "--output",
        required=True,
        type=pathlib.Path,
    )
    folder_parser.add_argument(
        "--all-files",
        action="store_true",
    )

    diff_parser = subparsers.add_parser("diff")
    diff_parser.add_argument(
        "--repo",
        default=".",
        type=pathlib.Path,
    )
    diff_parser.add_argument(
        "--range",
        dest="diff_range",
    )
    diff_parser.add_argument(
        "--output",
        required=True,
        type=pathlib.Path,
    )

    args = parser.parse_args()

    if args.mode == "folder":
        extensions = None if args.all_files else DEFAULT_EXTENSIONS

        export_folder(
            root=args.path.resolve(),
            output=args.output.resolve(),
            extensions=extensions,
        )

    elif args.mode == "diff":
        export_git_diff(
            repo=args.repo.resolve(),
            output=args.output.resolve(),
            diff_range=args.diff_range,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())