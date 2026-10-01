"""List local branches and worktrees whose work is already in main. Deletes nothing.

    uv run python -m scripts.merged_leftovers

GitHub deletes a merged pull request's branch on GitHub only; the local branch and any
worktree stay behind. A branch counts as merged when every commit on it is in origin/main or
has an identical change there, so cherry-picked work counts. A worktree with uncommitted
changes is reported as unsaved, never as a leftover.
"""

from __future__ import annotations

import subprocess


def git(*args: str) -> str:
    """Run git and return its output, raising on failure."""
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def in_main(ref: str) -> bool:
    """Whether every change on `ref` already exists in origin/main."""
    return not any(line.startswith("+") for line in git("cherry", "origin/main", ref).splitlines())


def main() -> int:
    """Print removal commands for merged leftovers, and warn about unsaved work."""
    git("fetch", "--prune", "--quiet")
    worktrees: dict[str, str] = {}
    path = ""
    for line in git("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line.startswith("branch refs/heads/"):
            worktrees[line.removeprefix("branch refs/heads/")] = path
    main_checkout = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    for branch in git("branch", "--format=%(refname:short)").splitlines():
        if branch == "main":
            continue
        path = worktrees.get(branch, "")
        if path and git("-C", path, "status", "--porcelain"):
            print(f"UNSAVED  {branch}: uncommitted changes in {path}")
        elif not in_main(branch):
            print(f"keep     {branch}: has work not in main")
        elif path and not main_checkout.startswith(path):
            print(f"merged   {branch}: git worktree remove {path}; git branch -D {branch}")
        elif path:
            print(f"merged   {branch}: checked out in the main checkout; switch to main first")
        else:
            print(f"merged   {branch}: git branch -D {branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
