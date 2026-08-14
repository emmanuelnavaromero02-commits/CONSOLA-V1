#!/usr/bin/env python3
"""Select the nearest ancestral strict-SemVer release tag.

Auxiliary tags are ignored completely.  The selected tag must resolve to a
commit reachable from the parent of the release commit, so the current tag can
never accidentally become its own comparison base.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path


SEMVER_RE = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        text=True,
        capture_output=True,
    )


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] | None

    @classmethod
    def parse(cls, tag: str) -> SemVer | None:
        match = SEMVER_RE.fullmatch(tag)
        if match is None:
            return None
        prerelease = match.group("pre")
        if prerelease is not None and any(
            identifier.isdigit()
            and len(identifier) > 1
            and identifier.startswith("0")
            for identifier in prerelease.split(".")
        ):
            return None
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            tuple(prerelease.split(".")) if prerelease is not None else None,
        )

    def _compare_prerelease(self, other: SemVer) -> int:
        left = self.prerelease
        right = other.prerelease
        if left is None:
            return 0 if right is None else 1
        if right is None:
            return -1
        for a, b in zip(left, right, strict=False):
            if a == b:
                continue
            a_numeric = a.isdigit()
            b_numeric = b.isdigit()
            if a_numeric and b_numeric:
                return -1 if int(a) < int(b) else 1
            if a_numeric != b_numeric:
                return -1 if a_numeric else 1
            return -1 if a < b else 1
        return (len(left) > len(right)) - (len(left) < len(right))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        left_core = (self.major, self.minor, self.patch)
        right_core = (other.major, other.minor, other.patch)
        if left_core != right_core:
            return left_core < right_core
        return self._compare_prerelease(other) < 0


def select_previous_release(
    head: str, current_tag: str | None = None, *, repo: Path | None = None
) -> tuple[str, str]:
    repo = repo or Path.cwd()
    head_commit = _git(repo, "rev-parse", f"{head}^{{commit}}").stdout.strip()
    parents = _git(repo, "rev-list", "--parents", "-n", "1", head_commit).stdout.split()
    if len(parents) == 1:
        return head_commit, "none"
    parent = parents[1]

    candidates: list[tuple[int, SemVer, str, str]] = []
    for tag in _git(repo, "tag", "--list").stdout.splitlines():
        version = SemVer.parse(tag)
        if version is None or tag == current_tag:
            continue
        resolved = _git(repo, "rev-parse", f"{tag}^{{commit}}", check=False)
        if resolved.returncode != 0:
            continue
        commit = resolved.stdout.strip()
        if _git(repo, "merge-base", "--is-ancestor", commit, parent, check=False).returncode:
            continue
        distance_text = _git(repo, "rev-list", "--count", f"{commit}..{parent}").stdout.strip()
        candidates.append((int(distance_text), version, tag, commit))

    if not candidates:
        roots = _git(repo, "rev-list", "--max-parents=0", head_commit).stdout.splitlines()
        if len(roots) != 1:
            raise RuntimeError("release history must have exactly one root commit")
        return roots[0], "none"

    nearest_distance = min(candidate[0] for candidate in candidates)
    nearest = [candidate for candidate in candidates if candidate[0] == nearest_distance]
    _distance, _version, tag, commit = max(nearest, key=lambda item: item[1])
    return commit, tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--current-tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    commit, tag = select_previous_release(args.head, args.current_tag)
    rendered = f"base={commit}\ntag={tag}\n"
    output_path = args.github_output
    if output_path is None and os.environ.get("GITHUB_OUTPUT"):
        output_path = Path(os.environ["GITHUB_OUTPUT"])
    if output_path is not None:
        with output_path.open("a", encoding="utf-8") as output:
            output.write(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
