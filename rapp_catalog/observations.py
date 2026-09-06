"""Append-only semantic observations. One atomically created event is one transaction."""

import json
from pathlib import Path

from .common import (
    atomic_bytes, boolean, branch_name, canonical, digest, enum, exact_keys,
    file_hash, hex_id, integer, license_id, load_json, public_repo_url,
    repo_name, require, safe_tree, timestamp,
)


AVAILABILITIES = {
    "not_polled", "public", "empty", "renamed_public", "not_listed_public", "observer_excluded",
}
ROW_KEYS = {
    "repository_id", "full_name", "public_url", "default_branch", "observed_head",
    "availability", "previous_names", "archived", "fork", "license_spdx",
}


def validate_observation(row):
    exact_keys(row, ROW_KEYS)
    integer(row["repository_id"], 1)
    name = repo_name(row["full_name"])
    require(public_repo_url(row["public_url"]) == "https://github.com/" + name,
            "observation_url_mismatch")
    branch_name(row["default_branch"], nullable=True)
    hex_id(row["observed_head"], nullable=True)
    enum(row["availability"], AVAILABILITIES)
    require(isinstance(row["previous_names"], list), "invalid_previous_names")
    for previous in row["previous_names"]:
        repo_name(previous)
        require(previous != name, "current_name_in_previous_names")
    require(row["previous_names"] == sorted(set(row["previous_names"])),
            "noncanonical_previous_names")
    for key in ("archived", "fork"):
        boolean(row[key])
    license_id(row["license_spdx"])
    if row["availability"] in {"not_polled", "empty", "observer_excluded"}:
        require(row["observed_head"] is None, "unexpected_observed_head")
    if row["availability"] == "public":
        require(row["observed_head"] is not None, "missing_observed_head")
    safe_tree(row)
    return row


def seed_state(manifest, tables):
    state = {}
    for row in tables["repos"]:
        if row[4] != "owner":
            continue
        observation = {
            "repository_id": row[1], "full_name": row[2], "public_url": row[3],
            "default_branch": row[6], "observed_head": None, "availability": "not_polled",
            "previous_names": [], "archived": row[8], "fork": row[9], "license_spdx": row[7],
        }
        state[row[1]] = validate_observation(observation)
    return state


def state_rows(state):
    return [state[rid] for rid in sorted(state)]


def state_digest(state):
    return digest(state_rows(state))


def initial_state(root, generations):
    path = Path(root) / "data" / "observations" / "baseline.json"
    require(path.parent.is_dir() and not path.parent.is_symlink() and not path.is_symlink(),
            "invalid_observation_baseline")
    baseline = load_json(path)
    exact_keys(baseline, {"schema", "generation_id", "generation_manifest_sha256"})
    require(baseline["schema"] == "rapp-observation-baseline/1", "observation_baseline_schema")
    for manifest, tables in generations:
        if manifest["generation_id"] == baseline["generation_id"]:
            path = Path(root) / "data" / "generations" / manifest["generation_id"] / "manifest.json"
            require(file_hash(path) == hex_id(baseline["generation_manifest_sha256"], 64),
                    "observation_baseline_mismatch")
            return seed_state(manifest, tables)
    require(False, "observation_baseline_missing")


def replay_events(root, baseline):
    directory = Path(root) / "data" / "observations"
    state = dict(baseline)
    events = []
    for path in sorted(directory.iterdir()):
        if path.name in {"baseline.json", ".gitkeep"}:
            continue
        sequence = len(events) + 1
        require(path.name == f"{sequence:010d}.json" and not path.is_symlink(),
                "observation_sequence_gap")
        require(path.stat().st_size < 16 * 1024 * 1024, "oversized_observation")
        event = load_json(path)
        exact_keys(event, {"schema", "sequence", "source", "observed_at", "previous_sha256",
                           "snapshot_sha256", "changes"})
        require(event["schema"] == "rapp-public-observation/1", "observation_schema")
        require(event["sequence"] == sequence, "observation_sequence_mismatch")
        enum(event["source"], {"github_public_api"})
        require(timestamp(event["observed_at"]) == event["observed_at"],
                "noncanonical_observation_time")
        if events:
            require(event["observed_at"] >= events[-1]["observed_at"], "observation_clock_regression")
        require(hex_id(event["previous_sha256"], 64) == state_digest(state),
                "observation_previous_hash_mismatch")
        require(isinstance(event["changes"], list) and event["changes"], "empty_observation")
        last_id = 0
        for change in event["changes"]:
            exact_keys(change, {"repository_id", "before_sha256", "after"})
            rid = integer(change["repository_id"], 1)
            require(rid > last_id, "unordered_observation_changes")
            last_id = rid
            before = state.get(rid)
            require(change["before_sha256"] == (digest(before) if before else None),
                    "observation_before_hash_mismatch")
            after = validate_observation(change["after"])
            require(after["repository_id"] == rid and before != after, "nonsemantic_observation")
            state[rid] = after
        require(hex_id(event["snapshot_sha256"], 64) == state_digest(state),
                "observation_snapshot_hash_mismatch")
        safe_tree(event)
        events.append(event)
    return state, events


def append_event(root, before, after, events, observed_at):
    require(set(before) <= set(after), "observation_deletion_forbidden")
    changes = []
    for rid in sorted(after):
        row = validate_observation(after[rid])
        old = before.get(rid)
        if old != row:
            changes.append({
                "repository_id": rid, "before_sha256": digest(old) if old else None, "after": row,
            })
    if not changes:
        return None
    observed_at = timestamp(observed_at)
    if events:
        require(observed_at >= events[-1]["observed_at"], "observation_clock_regression")
    sequence = len(events) + 1
    event = {
        "schema": "rapp-public-observation/1", "sequence": sequence,
        "source": "github_public_api", "observed_at": observed_at,
        "previous_sha256": state_digest(before), "snapshot_sha256": state_digest(after),
        "changes": changes,
    }
    safe_tree(event)
    body = (json.dumps(event, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
            + "\n").encode("ascii")
    require(len(body) < 16 * 1024 * 1024, "oversized_observation")
    path = Path(root) / "data" / "observations" / f"{sequence:010d}.json"
    require(not path.exists(), "observation_already_exists")
    atomic_bytes(path, body)
    return event
