"""CI only: commit named public snapshots/events, with explicit no-op/error semantics."""

import json
import re
import subprocess
import sys


class GitFailure(Exception):
    pass


def git(*args, allowed=(0,)):
    result = subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=180, check=False)
    if result.returncode not in allowed:
        raise GitFailure("git_command_failed")
    return result


def commit_observations():
    status = git("status", "--porcelain=v1", "--untracked-files=all", "-z").stdout
    paths = []
    for record in status.split(b"\0"):
        if not record:
            continue
        event = re.fullmatch(rb"\?\? data/observations/[0-9]{10}\.json", record)
        snapshot = re.fullmatch(rb"(?:\?\?| M) snapshots/(?:repositories|world)\.json", record)
        if not event and not snapshot:
            raise GitFailure("unexpected_worktree_change")
        paths.append(record[3:].decode("ascii"))
    if paths:
        git("add", "--", *sorted(paths))
    result = git("diff", "--cached", "--quiet", allowed=(0, 1))
    if result.returncode == 0:
        return {"semantic_commit": False}
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("commit", "-m", "data: update public RAPP snapshots")
    git("push", "origin", "HEAD:main")
    return {"semantic_commit": True}


def main():
    try:
        print(json.dumps(commit_observations(), sort_keys=True))
        return 0
    except (GitFailure, OSError, subprocess.SubprocessError):
        print('{"error":"observation_commit_failed"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
