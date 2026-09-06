"""Versioned, allowlisted public tables. JSONL rows use the declared column order."""

import collections
import json
from pathlib import Path

from .common import (
    boolean, canonical, enum, exact_keys, file_hash, generation_id, hex_id,
    integer, license_id, load_json, path_fields, public_repo_url, relative_name,
    repo_name, require, safe_tree, timestamp, branch_name,
)


COLUMNS = {
    "repos": [
        "unit_id", "repository_id", "full_name", "public_url", "scope",
        "captured_commit", "default_branch", "license_spdx", "archived", "fork",
        "empty", "tracked_entries", "rapp_evidence", "compatibility",
        "entry_binding_sha256",
    ],
    "files": ["unit_id", "path", "path_bytes_base64", "mode", "git_object"],
    "blobs": [
        "git_object", "sha256", "bytes", "bytes_hashed", "content_status",
        "integrity", "reference_count",
    ],
    "dependency_links": [
        "parent_unit_id", "path", "path_bytes_base64", "commit", "public_url",
        "status", "dependency_unit_id",
    ],
    "status_counts": ["unit_id", "category", "status", "count"],
}

CONTENT_STATUSES = {
    "opaque_archive", "opaque_non_utf8_or_nul", "scanned_utf8",
    "symlink_target_not_dereferenced", "unmeasured_size_bound",
}
MODES = {"100644", "100755", "120000", "160000"}
ARTIFACT_TYPES = {"egg", "frame", "identity"}
ARTIFACT_OUTCOMES = {
    "accepted", "refused", "unverified_trust", "unverified_history",
    "accepted_grammar_only",
}
DECLARATIONS = {
    "declared_legacy_rapp", "declared_rapp1", "filename_only_or_unidentified",
    "other_rapp_namespace", "rappid_field_only", "unclaimed_or_other_protocol",
}
COUNT_KEYS = {
    "owner_repos", "dependency_repos", "owner_files", "dependency_files",
    "blobs", "gitlinks", "unavailable_gitlinks", "source_blob_bytes",
}
PROVENANCE_KEYS = {
    "scope_sha256", "genome_index_sha256", "dependency_index_sha256",
    "matrix_sha256", "carrier_receipt_sha256", "matrix_authority_commit",
    "matrix_authority_revision",
}


def check_unit(value):
    import re
    require(isinstance(value, str) and re.fullmatch(r"(repo:[1-9][0-9]*|dep:[0-9a-f]{24})",
                                                   value), "invalid_unit_id")
    return value


def status_key(category, status):
    require(isinstance(status, str), "invalid_status_key")
    if category == "entry_coverage":
        enum(status, CONTENT_STATUSES | {"gitlink_not_an_owner_blob"})
    elif category == "entry_mode":
        enum(status, MODES)
    elif category == "blob_integrity":
        enum(status, {"verified_git_blob"})
    elif category == "blob_content":
        enum(status, CONTENT_STATUSES)
    elif category == "stream_outcome":
        enum(status, {"accepted_observed_chain", "refused", "unverified"})
    elif category in ("artifact_outcome", "artifact_declaration"):
        pieces = status.split(":")
        expected = 3 if category == "artifact_declaration" else 2
        require(len(pieces) == expected, "invalid_status_key")
        if expected == 3:
            enum(pieces.pop(0), DECLARATIONS)
        enum(pieces[0], ARTIFACT_TYPES)
        enum(pieces[1], ARTIFACT_OUTCOMES)
    else:
        require(False, "invalid_status_category")


def validate_tables(manifest, tables):
    repos = {}
    counts = collections.Counter()
    for row in tables["repos"]:
        (unit, rid, name, url, scope, commit, branch, license_spdx, archived, fork,
         empty, entries, evidence, compatibility, binding) = row
        check_unit(unit)
        require(unit not in repos, "duplicate_repository")
        repo_name(name)
        require(public_repo_url(url) == url == "https://github.com/" + name,
                "repository_url_mismatch")
        enum(scope, {"owner", "dependency"})
        if rid is not None:
            integer(rid, 1)
        if scope == "owner":
            require(unit == f"repo:{integer(rid, 1)}", "repository_id_mismatch")
        hex_id(commit, nullable=True)
        branch_name(branch, nullable=True)
        license_id(license_spdx)
        for value in (archived, fork, empty, evidence):
            if value is not None:
                boolean(value)
        require(type(empty) is bool and type(evidence) is bool, "invalid_boolean")
        integer(entries)
        require(empty == (commit is None and entries == 0), "empty_repository_mismatch")
        enum(compatibility, {"not_established"})
        hex_id(binding, 64)
        repos[unit] = row
        counts[scope + "_repos"] += 1

    blobs = {}
    for row in tables["blobs"]:
        obj, sha, size, hashed, status, integrity, references = row
        hex_id(obj)
        hex_id(sha, 64)
        integer(size)
        require(integer(hashed) == size, "incomplete_blob_hashing")
        enum(status, CONTENT_STATUSES)
        enum(integrity, {"verified_git_blob"})
        integer(references, 1)
        require(obj not in blobs, "duplicate_blob")
        blobs[obj] = row
        counts["source_blob_bytes"] += size
    counts["blobs"] = len(blobs)

    seen = set()
    references = collections.Counter()
    entry_counts = collections.Counter()
    gitlinks = {}
    for unit, path, raw_path, mode, obj in tables["files"]:
        require(unit in repos, "orphan_file")
        require((path, raw_path) == path_fields(raw_path), "path_display_mismatch")
        enum(mode, MODES)
        hex_id(obj)
        key = (unit, raw_path)
        require(key not in seen, "duplicate_file")
        seen.add(key)
        scope = repos[unit][4]
        counts[scope + "_files"] += 1
        entry_counts[unit] += 1
        if mode == "160000":
            gitlinks[key] = obj
            counts["gitlinks"] += 1
        else:
            require(obj in blobs, "missing_blob_metadata")
            references[obj] += 1
    require(set(references) == set(blobs), "blob_coverage_mismatch")
    for obj, row in blobs.items():
        require(references[obj] == row[6], "blob_reference_mismatch")
    for unit, row in repos.items():
        require(entry_counts[unit] == row[11], "repository_entry_count_mismatch")

    seen_links = set()
    linked_dependencies = set()
    for unit, path, raw_path, commit, url, status, dependency in tables["dependency_links"]:
        key = (unit, raw_path)
        require(key in gitlinks and key not in seen_links, "gitlink_coverage_mismatch")
        require((path, raw_path) == path_fields(raw_path), "path_display_mismatch")
        require(hex_id(commit) == gitlinks[key], "gitlink_commit_mismatch")
        enum(status, {"hydrated", "unavailable"})
        if url is not None:
            require(public_repo_url(url) == url, "dependency_url_mismatch")
        if status == "hydrated":
            require(dependency in repos and repos[dependency][4] == "dependency",
                    "missing_dependency")
            require(repos[dependency][5] == commit and repos[dependency][3] == url,
                    "dependency_binding_mismatch")
            linked_dependencies.add(dependency)
        else:
            require(dependency is None, "unavailable_dependency_binding")
            counts["unavailable_gitlinks"] += 1
        seen_links.add(key)
    require(seen_links == set(gitlinks), "gitlink_coverage_mismatch")
    require(linked_dependencies == {key for key, row in repos.items() if row[4] == "dependency"},
            "dependency_coverage_mismatch")

    seen_statuses = set()
    coverage = collections.Counter()
    modes = collections.Counter()
    for unit, category, status, count in tables["status_counts"]:
        require(unit is None or unit in repos, "orphan_status")
        status_key(category, status)
        integer(count, 1)
        key = (unit, category, status)
        require(key not in seen_statuses, "duplicate_status")
        seen_statuses.add(key)
        if unit is not None and category == "entry_coverage":
            coverage[unit] += count
        if unit is not None and category == "entry_mode":
            modes[unit] += count
    for unit in repos:
        require(coverage[unit] == modes[unit] == entry_counts[unit],
                "status_entry_coverage_mismatch")
    require(dict(manifest["counts"]) == {key: counts[key] for key in COUNT_KEYS},
            "source_projection_count_mismatch")


def read_generation(directory):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "invalid_generation_directory")
    require(all(path.is_file() and not path.is_symlink() for path in directory.iterdir()),
            "invalid_generation_file")
    manifest = load_json(directory / "manifest.json")
    exact_keys(manifest, {
        "schema", "generation_id", "parent_generation_id", "captured_at",
        "inventory_status", "runtime_compatibility", "provenance", "counts", "tables",
    })
    require(manifest["schema"] == "rapp-public-generation/1", "generation_schema")
    generation_id(manifest["generation_id"])
    require(directory.name == manifest["generation_id"], "generation_directory_mismatch")
    if manifest["parent_generation_id"] is not None:
        generation_id(manifest["parent_generation_id"])
        require(manifest["parent_generation_id"] != manifest["generation_id"], "generation_cycle")
    timestamp(manifest["captured_at"])
    enum(manifest["inventory_status"], {"complete_frozen_scope"})
    enum(manifest["runtime_compatibility"], {"not_established"})
    exact_keys(manifest["provenance"], PROVENANCE_KEYS)
    for key, value in manifest["provenance"].items():
        if key.endswith("_sha256"):
            hex_id(value, 64)
        elif key == "matrix_authority_commit":
            hex_id(value)
        else:
            import re
            require(isinstance(value, str) and re.fullmatch(r"rev-[1-9][0-9]*", value),
                    "invalid_matrix_authority_revision")
    exact_keys(manifest["counts"], COUNT_KEYS)
    for value in manifest["counts"].values():
        integer(value)
    exact_keys(manifest["tables"], COLUMNS)
    safe_tree(manifest)
    expected_files = {"manifest.json"}
    tables = {}
    for table, columns in COLUMNS.items():
        descriptor = manifest["tables"][table]
        exact_keys(descriptor, {"columns", "rows", "partitions"})
        require(descriptor["columns"] == columns, "table_columns_mismatch")
        integer(descriptor["rows"])
        require(isinstance(descriptor["partitions"], list), "invalid_partition_list")
        rows = []
        previous = None
        for part in descriptor["partitions"]:
            exact_keys(part, {"file", "rows", "bytes", "sha256"})
            name = relative_name(part["file"])
            require(name not in expected_files, "duplicate_partition")
            expected_files.add(name)
            path = directory / name
            require(not path.is_symlink(), "symlink_input")
            require(integer(part["bytes"], 1) < 50 * 1024 * 1024, "oversized_partition")
            require(path.stat().st_size == part["bytes"], "partition_size_mismatch")
            require(file_hash(path) == hex_id(part["sha256"], 64), "partition_hash_mismatch")
            row_count = 0
            with path.open("rb") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError) as exc:
                        from .common import CatalogError
                        raise CatalogError("invalid_jsonl") from exc
                    require(isinstance(row, list) and len(row) == len(columns),
                            "invalid_row_shape")
                    require(canonical(row) == line, "noncanonical_jsonl")
                    require(previous is None or line > previous, "unsorted_or_duplicate_rows")
                    previous = line
                    safe_tree(row)
                    rows.append(row)
                    row_count += 1
            require(row_count == integer(part["rows"], 1), "partition_row_count_mismatch")
        require(len(rows) == descriptor["rows"], "table_row_count_mismatch")
        tables[table] = rows
    require({p.name for p in directory.iterdir()} == expected_files,
            "unlisted_generation_file")
    validate_tables(manifest, tables)
    return manifest, tables


def read_generations(root):
    directory = Path(root) / "data" / "generations"
    require(directory.is_dir() and not directory.is_symlink()
            and not directory.parent.is_symlink(), "missing_generation_data")
    result = []
    for path in sorted(directory.iterdir()):
        require(path.is_dir() and not path.is_symlink(), "invalid_generation_directory")
        result.append(read_generation(path))
    require(bool(result), "missing_generation_data")
    return result
