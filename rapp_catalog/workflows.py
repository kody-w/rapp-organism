"""Allowlisted reported workflow measurements, not runtime or authority certifications."""

import base64
import datetime as dt
from pathlib import Path
import re
import urllib.parse

from .common import (
    branch_name, enum, exact_keys, hex_id, integer, load_json, path_fields,
    public_repo_url, public_text, require, safe_tree, timestamp,
)


MEASURED_BOOLEANS = {
    "fresh_public_checkout": True, "private_bootstrap_or_prior_context": False,
    "recovered_source_bytes_equal": True, "unchanged_repeat_observed": True,
    "overwrite_refused": True, "recovered_code_executed": False,
}
MEASURED_COUNTS = {
    "source_repairs", "emitted_frames_verified", "byte_comparison_exit_code",
    "bridge_contracts_passed", "native_frames_checked", "native_chains_checked",
}
SCOPE_BOOLEANS = {
    "unsigned_local_snapshot_bridge": True, "separate_runtime_and_directory": True,
    "different_physical_machines": False, "os_enforced_sandbox": False,
    "authenticated_consumer_acceptance": False,
    "full_organism_runtime_compatibility": False,
    "historical_native_records_rewritten": False,
}
OBSERVATION_COLUMNS = [
    "observation_id", "observed_date", "workflow", "result", "public_starting_point",
    "source_repository", "source_commit", "source_path", "source_bytes", "source_sha256",
    "reference_repository", "reference_commit", "reference_rapp_py_sha256",
    "producer", "receiver", "source_summary_sha256", "reporting_basis",
]
INTEGRATION_COLUMNS = [
    "observation_id", "repository", "tested_source_commit", "pull_request_number",
    "pull_request_url", "merge_commit", "target_branch", "merged_at", "state",
    "reference_protocol_ratified",
]


def validate_workflow(value):
    exact_keys(value, {"schema", "observation_id", "observed_date", "workflow", "result",
                       "public_starting_point", "source", "reference_candidate", "measured",
                       "scope", "source_summary_sha256", "reporting_basis"})
    require(value["schema"] == "rapp-catalog-workflow-observation/1", "workflow_schema")
    public_text(value["observation_id"], 100)
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]*", value["observation_id"]), "invalid_workflow_id")
    public_text(value["observed_date"], 10)
    try:
        date = dt.date.fromisoformat(value["observed_date"])
    except ValueError as error:
        from .common import CatalogError
        raise CatalogError("invalid_workflow_date") from error
    require(date.isoformat() == value["observed_date"], "invalid_workflow_date")
    enum(value["workflow"], {"native-dogg-tick-to-rapp1-bridge-to-node-byte-recovery"})
    enum(value["result"], {"success-within-declared-scope"})
    enum(value["reporting_basis"], {"public_safe_independent_summary_only"})
    hex_id(value["source_summary_sha256"], 64)
    source = value["source"]
    exact_keys(source, {"repository", "commit", "path", "bytes", "sha256"})
    require(public_repo_url(source["repository"]) == source["repository"], "workflow_repository_url")
    hex_id(source["commit"])
    hex_id(source["sha256"], 64)
    require(integer(source["bytes"], 1) < 2 ** 63, "workflow_count_out_of_range")
    public_text(source["path"], 16384)
    require(path_fields(base64.b64encode(source["path"].encode("utf-8")).decode("ascii"))[0]
            == source["path"], "workflow_source_path")
    starting = public_text(value["public_starting_point"], 1024)
    prefix = source["repository"] + "/tree/"
    require(starting.startswith(prefix), "workflow_starting_point")
    url = urllib.parse.urlsplit(starting)
    require(not url.query and not url.fragment, "workflow_starting_point")
    branch_name(starting[len(prefix):])
    candidate = value["reference_candidate"]
    exact_keys(candidate, {"repository", "commit", "rapp_py_sha256", "accepted_new_protocol_revision"})
    require(public_repo_url(candidate["repository"]) == candidate["repository"], "workflow_repository_url")
    hex_id(candidate["commit"])
    hex_id(candidate["rapp_py_sha256"], 64)
    require(candidate["accepted_new_protocol_revision"] is False, "workflow_not_authority_ratification")
    measured = value["measured"]
    exact_keys(measured, set(MEASURED_BOOLEANS) | MEASURED_COUNTS | {"producer", "receiver"})
    for key, expected in MEASURED_BOOLEANS.items():
        require(measured[key] is expected, "workflow_measurement_scope_mismatch")
    for key in MEASURED_COUNTS:
        require(integer(measured[key]) < 2 ** 63, "workflow_count_out_of_range")
    require(measured["source_repairs"] == 0 and measured["byte_comparison_exit_code"] == 0
            and measured["emitted_frames_verified"] == 1, "workflow_result_mismatch")
    for key, pattern in (("producer", r"CPython [0-9]+\.[0-9]+\.[0-9]+"),
                         ("receiver", r"Node\.js [0-9]+\.[0-9]+\.[0-9]+")):
        public_text(measured[key], 64)
        require(re.fullmatch(pattern, measured[key]), "workflow_runtime_label")
    scope = value["scope"]
    exact_keys(scope, set(SCOPE_BOOLEANS) | {"physical_machine_count"})
    require(integer(scope["physical_machine_count"], 1) == 1, "workflow_one_machine_only")
    for key, expected in SCOPE_BOOLEANS.items():
        require(scope[key] is expected, "workflow_scope_widening_refused")
    safe_tree(value)
    return value


def read_workflows(root):
    directory = Path(root) / "data" / "workflows"
    require(directory.is_dir() and not directory.is_symlink(), "workflow_directory_missing")
    rows = []
    identities = set()
    for path in sorted(directory.iterdir()):
        require(path.is_file() and not path.is_symlink()
                and re.fullmatch(r"[a-z0-9-]+\.json", path.name), "invalid_workflow_file")
        require(path.stat().st_size <= 64 * 1024, "workflow_observation_too_large")
        row = validate_workflow(load_json(path))
        require(row["observation_id"] not in identities, "duplicate_workflow_observation")
        identities.add(row["observation_id"])
        rows.append(row)
    return sorted(rows, key=lambda row: row["observation_id"])


def observation_row(value):
    source, candidate, measured = value["source"], value["reference_candidate"], value["measured"]
    return (
        value["observation_id"], value["observed_date"], value["workflow"], value["result"],
        value["public_starting_point"], source["repository"], source["commit"],
        source["path"], source["bytes"], source["sha256"], candidate["repository"],
        candidate["commit"], candidate["rapp_py_sha256"], measured["producer"],
        measured["receiver"], value["source_summary_sha256"], value["reporting_basis"],
    )


def measurement_rows(value):
    identity = value["observation_id"]
    for key in sorted(set(MEASURED_BOOLEANS) | MEASURED_COUNTS):
        yield identity, "measured", key, int(value["measured"][key])
    for key in sorted(set(SCOPE_BOOLEANS) | {"physical_machine_count"}):
        yield identity, "scope", key, int(value["scope"][key])
    yield identity, "reference", "accepted_new_protocol_revision", 0


def read_integrations(root, observations):
    path = Path(root) / "data" / "workflow-integrations.json"
    require(path.is_file() and not path.is_symlink(), "invalid_workflow_integrations")
    require(path.stat().st_size <= 1024 * 1024, "workflow_integrations_too_large")
    value = load_json(path)
    exact_keys(value, {"schema", "rows"})
    require(value["schema"] == "rapp-public-workflow-integrations/1", "workflow_integration_schema")
    require(isinstance(value["rows"], list), "invalid_workflow_integrations")
    known = {row["observation_id"]: row for row in observations}
    seen = set()
    for row in value["rows"]:
        exact_keys(row, INTEGRATION_COLUMNS)
        identity = row["observation_id"]
        require(identity in known and identity not in seen, "unbound_workflow_integration")
        source = known[identity]["source"]
        require(public_repo_url(row["repository"]) == row["repository"] == source["repository"],
                "workflow_integration_repository_mismatch")
        require(hex_id(row["tested_source_commit"]) == source["commit"],
                "workflow_tested_commit_replaced")
        number = integer(row["pull_request_number"], 1)
        require(row["pull_request_url"] == row["repository"] + f"/pull/{number}",
                "workflow_integration_pr_url")
        hex_id(row["merge_commit"])
        branch_name(row["target_branch"])
        require(timestamp(row["merged_at"]) == row["merged_at"], "workflow_integration_time")
        enum(row["state"], {"merged"})
        require(row["reference_protocol_ratified"] is False, "integration_not_reference_ratification")
        safe_tree(row)
        seen.add(identity)
    return sorted(value["rows"], key=lambda row: row["observation_id"])
