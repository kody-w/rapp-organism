"""One-time projection of reviewed capture metadata, never of source bodies."""

import collections
import hashlib
from pathlib import Path

from .common import (
    boolean, canonical, digest, enum, file_hash, generation_id, hex_id, integer,
    license_id, load_json, path_fields, public_repo_url, repo_name, require,
    safe_tree, timestamp, workspace,
)
from .model import COLUMNS, CONTENT_STATUSES, MODES, status_key, validate_tables


PARTITION_BYTES = 4 * 1024 * 1024


def dependency_key(url, commit):
    return "dep:" + hashlib.sha256((url + "@" + commit).encode("ascii")).hexdigest()[:24]


def unique_index(rows, key):
    require(isinstance(rows, list), "invalid_source_rows")
    result = {}
    for row in rows:
        require(isinstance(row, dict), "invalid_source_row")
        identity = row[key]
        require(identity not in result, "duplicate_source_identity")
        result[identity] = row
    return result


def make_projection(scope, genome, dependency_index, matrix, receipt, generation, parent,
                    source_hashes):
    generation_id(generation)
    if parent is not None:
        generation_id(parent)
    for source, schema in (
        (scope, "rapp-public-source-scope/1"),
        (genome, "rapp-full-genome-index/1"),
        (dependency_index, "rapp-public-gitlink-bodies/1"),
        (matrix, "rapp-captured-estate-inventory/1"),
    ):
        require(source.get("schema") == schema, "source_schema_mismatch")
    require(matrix["report_status"] == "complete" and matrix["capture_errors"] == [],
            "incomplete_source_matrix")
    require(matrix["compatibility_verdict"] == "not_established", "source_compatibility_changed")
    require(receipt["status"] == "VERIFIED", "unverified_carrier_receipt")
    hex_id(receipt["archive"]["sha256"], 64)
    integer(receipt["archive"]["bytes"], 1)
    hex_id(receipt["frame_hash"], 64)
    owner = repo_name(scope["owner"], full=False)
    scope_repos = unique_index(scope["repositories"], "repository_id")
    genome_repos = unique_index(genome["repositories"], "repository_id")
    matrix_repos = unique_index(matrix["repositories"], "repository_id")
    require(set(scope_repos) == set(genome_repos) == set(matrix_repos),
            "source_repository_coverage_mismatch")
    require(len(scope_repos) == scope["public_repositories"], "scope_count_mismatch")
    require(genome["source_scope"] == scope, "embedded_scope_mismatch")
    tables = {table: [] for table in COLUMNS}
    unit_names = {}
    unit_sources = {}
    matrix_units = {}

    for rid, source in scope_repos.items():
        integer(rid, 1)
        require(source.get("private", False) is False, "private_source")
        require(source.get("visibility", "public") == "public", "private_source")
        captured = genome_repos[rid]
        measured = matrix_repos[rid]
        name = repo_name(source["name"], full=False)
        url = public_repo_url(source["public_url"])
        require(url == "https://github.com/" + owner + "/" + name, "source_owner_mismatch")
        require(captured["repo"] == measured["repository"] == name, "source_name_mismatch")
        require(public_repo_url(captured["public_url"]) == public_repo_url(measured["public_url"])
                == url, "source_url_mismatch")
        commit = hex_id(source["captured_commit"], nullable=True)
        require(commit == captured["source_snapshot_commit"] == measured["captured_commit"],
                "source_commit_mismatch")
        branch = source["captured_default_branch"]
        require(branch == captured["default_branch"] == measured["captured_default_branch"],
                "source_branch_mismatch")
        require(source["license_detection"] == captured["license_detection"],
                "source_license_mismatch")
        require(source["archived"] == measured["archived"]
                and source["fork"] == measured["fork"], "source_flags_mismatch")
        require(captured["lfs_objects"] == [], "unhandled_lfs_metadata")
        require(captured["empty"] == measured["empty"], "source_empty_mismatch")
        unit = "repo:" + str(rid)
        unit_names[name] = unit
        unit_sources[unit] = captured["entries"]
        matrix_units[unit] = measured
        tables["repos"].append([
            unit, rid, owner + "/" + name, url, "owner", commit, branch,
            license_id(source["license_detection"]), boolean(source["archived"]),
            boolean(source["fork"]), boolean(captured["empty"]),
            integer(measured["tracked_entries"]), boolean(measured["observed_rapp_evidence"]),
            enum(measured["compatibility"], {"not_established"}),
            hex_id(measured["entry_binding_sha256"], 64),
        ])

    measured_dependencies = {}
    for measured in matrix["dependencies"]:
        url = public_repo_url(measured["source_url"])
        commit = hex_id(measured["captured_commit"])
        key = (url, commit)
        require(key not in measured_dependencies, "duplicate_source_dependency")
        measured_dependencies[key] = measured
    seen_dependencies = set()
    for dependency in dependency_index["dependencies"]:
        url = public_repo_url(dependency["source_url"])
        commit = hex_id(dependency["commit"])
        key = (url, commit)
        require(key in measured_dependencies and key not in seen_dependencies,
                "source_dependency_coverage_mismatch")
        measured = measured_dependencies[key]
        seen_dependencies.add(key)
        unit = dependency_key(url, commit)
        unit_sources[unit] = dependency["entries"]
        matrix_units[unit] = measured
        tables["repos"].append([
            unit, None, url.removeprefix("https://github.com/"), url, "dependency",
            commit, None, None, None, None, False, integer(measured["tracked_entries"]),
            boolean(measured["observed_rapp_evidence"]),
            enum(measured["compatibility"], {"not_established"}),
            hex_id(measured["entry_binding_sha256"], 64),
        ])
    require(seen_dependencies == set(measured_dependencies), "source_dependency_coverage_mismatch")

    actual_modes = {}
    for unit, entries in unit_sources.items():
        actual_modes[unit] = collections.Counter()
        for entry in entries:
            path, raw_path = path_fields(entry["path_bytes_base64"])
            mode = enum(entry["mode"], MODES)
            require(entry["type"] == ("commit" if mode == "160000" else "blob"),
                    "source_entry_type_mismatch")
            tables["files"].append([unit, path, raw_path, mode, hex_id(entry["git_object"])])
            actual_modes[unit][mode] += 1
        require(len(entries) == matrix_units[unit]["tracked_entries"],
                "source_entry_count_mismatch")
        require(dict(actual_modes[unit]) == matrix_units[unit]["entry_modes"],
                "source_mode_count_mismatch")

    for blob in matrix["blobs"]:
        tables["blobs"].append([
            hex_id(blob["git_object"]), hex_id(blob["sha256"], 64),
            integer(blob["bytes"]), integer(blob["bytes_hashed"]),
            enum(blob["content_status"], CONTENT_STATUSES),
            enum(blob["integrity"], {"verified_git_blob"}), integer(blob["references"], 1),
        ])

    source_links = unique_index(
        [{**row, "_key": (row["parent_repo"], row["path"], row["commit"])}
         for row in dependency_index["links"]], "_key")
    measured_links = unique_index(
        [{**row, "_key": (row["parent_repo"], row["path"], row["commit"])}
         for row in matrix["dependency_links"]], "_key")
    require(source_links == measured_links, "source_gitlink_mismatch")
    file_links = {}
    for unit, path, raw_path, mode, obj in tables["files"]:
        if mode == "160000":
            file_links[(unit, path, obj)] = raw_path
    for link in dependency_index["links"]:
        unit = unit_names.get(link["parent_repo"])
        commit = hex_id(link["commit"])
        raw_path = file_links.get((unit, link["path"], commit))
        require(raw_path is not None, "unbound_source_gitlink")
        path, raw_path = path_fields(raw_path)
        status = enum(link["status"], {"hydrated", "pre-existing-unavailable"})
        url = public_repo_url(link["url"]) if link["url"] is not None else None
        dependency = dependency_key(url, commit) if status == "hydrated" else None
        tables["dependency_links"].append([
            unit, path, raw_path, commit, url,
            "hydrated" if status == "hydrated" else "unavailable", dependency,
        ])

    for unit, measured in matrix_units.items():
        groups = {
            "entry_coverage": measured["entry_coverage"],
            "entry_mode": measured["entry_modes"],
            "artifact_outcome": measured["artifact_outcomes"],
            "artifact_declaration": measured["artifact_declaration_outcomes"],
            "stream_outcome": collections.Counter(row["status"] for row in measured["stream_checks"]),
        }
        for category, statuses in groups.items():
            for status, count in statuses.items():
                status_key(category, status)
                tables["status_counts"].append([unit, category, status, integer(count, 1)])
    coverage = matrix["coverage"]
    for key, category in (("blob_integrity", "blob_integrity"),
                          ("unique_blob_content_coverage", "blob_content")):
        for status, count in coverage[key].items():
            status_key(category, status)
            tables["status_counts"].append([None, category, status, integer(count, 1)])

    owner_objects = {row[4] for row in tables["files"]
                     if row[0].startswith("repo:") and row[3] != "160000"}
    blob_sizes = {row[0]: row[2] for row in tables["blobs"]}
    require(len(owner_objects) == genome["counts"]["unique_blobs"]
            == coverage["owner_unique_blobs"], "source_owner_blob_count_mismatch")
    require(sum(blob_sizes[obj] for obj in owner_objects) == genome["counts"]["source_blob_bytes"]
            == coverage["owner_unique_blob_bytes_stat"], "source_owner_bytes_mismatch")
    require(genome["counts"]["tracked_entries"] == coverage["owner_tracked_entries_expected"]
            == coverage["owner_tracked_entries_accounted"], "source_owner_entry_count_mismatch")
    require(genome["counts"]["repositories"] == len(scope_repos)
            == coverage["owner_repositories_expected"] == coverage["owner_repositories_accounted"],
            "source_owner_count_mismatch")
    require(dependency_index["extra_blob_count"] == len(blob_sizes) - len(owner_objects),
            "source_dependency_blob_count_mismatch")
    require(dependency_index["unavailable_count"] == coverage["gitlinks_unavailable"],
            "source_unavailable_count_mismatch")
    require(coverage["object_store_file_names"] == coverage["owner_and_dependency_unique_blobs"],
            "source_object_store_count_mismatch")
    manifest = {
        "schema": "rapp-public-generation/1",
        "generation_id": generation,
        "parent_generation_id": parent,
        "captured_at": timestamp(scope["created_at"]),
        "inventory_status": "complete_frozen_scope",
        "runtime_compatibility": "not_established",
        "provenance": {
            **source_hashes,
            "matrix_authority_commit": hex_id(matrix["canonical"]["anchor_selected_commit"]),
            "matrix_authority_revision": matrix["canonical"]["revision"],
        },
        "counts": {
            "owner_repos": len(scope_repos),
            "dependency_repos": coverage["dependency_bodies_accounted"],
            "owner_files": coverage["owner_tracked_entries_accounted"],
            "dependency_files": coverage["dependency_tracked_entries_accounted"],
            "blobs": coverage["owner_and_dependency_unique_blobs"],
            "gitlinks": coverage["gitlinks"],
            "unavailable_gitlinks": coverage["gitlinks_unavailable"],
            "source_blob_bytes": coverage["bytes_hashed"],
        },
        "tables": {},
    }
    validate_tables(manifest, tables)
    for table, rows in tables.items():
        rows.sort(key=canonical)
        require(len({canonical(row) for row in rows}) == len(rows), "duplicate_source_rows")
        for row in rows:
            safe_tree(row)
    safe_tree(manifest)
    return manifest, tables


def write_projection(directory, manifest, tables):
    directory = Path(directory)
    for table, rows in tables.items():
        parts = []
        current = bytearray()
        count = 0

        def finish():
            if not current:
                return
            name = f"{table}-{len(parts) + 1:04d}.jsonl"
            (directory / name).write_bytes(current)
            parts.append({
                "file": name, "rows": count, "bytes": len(current),
                "sha256": hashlib.sha256(current).hexdigest(),
            })

        for row in rows:
            encoded = canonical(row)
            require(len(encoded) <= PARTITION_BYTES, "oversized_metadata_row")
            if current and len(current) + len(encoded) > PARTITION_BYTES:
                finish()
                current = bytearray()
                count = 0
            current.extend(encoded)
            count += 1
        finish()
        manifest["tables"][table] = {
            "columns": COLUMNS[table], "rows": len(rows), "partitions": parts,
        }
    (directory / "manifest.json").write_bytes(canonical(manifest))


def import_seed(root, *, scope, genome, dependencies, matrix, receipt, generation="g0002",
                parent="g0001", expect_matrix_sha256=None):
    paths = {
        "scope_sha256": scope, "genome_index_sha256": genome,
        "dependency_index_sha256": dependencies, "matrix_sha256": matrix,
        "carrier_receipt_sha256": receipt,
    }
    hashes = {key: file_hash(path) for key, path in paths.items()}
    if expect_matrix_sha256 is not None:
        require(hashes["matrix_sha256"] == hex_id(expect_matrix_sha256, 64),
                "unexpected_matrix_hash")
    manifest, tables = make_projection(
        load_json(scope), load_json(genome), load_json(dependencies), load_json(matrix),
        load_json(receipt), generation, parent, hashes,
    )
    parent_dir = Path(root) / "data" / "generations"
    require(not parent_dir.parent.is_symlink() and not parent_dir.is_symlink(), "symlink_output")
    parent_dir.mkdir(parents=True, exist_ok=True)
    destination = parent_dir / generation_id(generation)
    require(not destination.is_symlink(), "symlink_output")
    with workspace(Path(root), "seed") as work:
        stage = work / generation
        stage.mkdir()
        write_projection(stage, manifest, tables)
        from .model import read_generation
        read_generation(stage)
        if destination.exists():
            read_generation(destination)
            old = {path.name: file_hash(path) for path in destination.iterdir() if path.is_file()}
            new = {path.name: file_hash(path) for path in stage.iterdir()}
            require(old == new, "immutable_generation_conflict")
            changed = False
        else:
            stage.rename(destination)
            changed = True
    return {"changed": changed, "generation_id": generation, "counts": manifest["counts"],
            "manifest_sha256": digest(manifest), "source_hashes": hashes}
