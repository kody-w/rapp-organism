"""Deterministic SQLite and static query products, built entirely from public text."""

import csv
import datetime as dt
import json
from pathlib import Path
import shutil
import sqlite3
import urllib.parse

from .common import (
    CatalogError, canonical, digest, enum, exact_keys, file_hash, hex_id, integer,
    load_json, read_config, replace_directory, require, safe_tree, timestamp, workspace,
)
from .distribution import read_authority, read_distribution
from .model import COLUMNS, read_generations
from .observations import initial_state, replay_events, state_digest, state_rows
from .workflows import (
    INTEGRATION_COLUMNS, OBSERVATION_COLUMNS, measurement_rows, observation_row,
    read_integrations, read_workflows,
)


PRESETS = [
    {
        "id": "coverage", "title": "Complete captured scope",
        "sql": "SELECT * FROM catalog_summary",
    },
    {
        "id": "heads", "title": "Carried commits versus observed public heads",
        "sql": "SELECT full_name, captured_commit, observed_public_head, availability, head_relation "
               "FROM repo_observations WHERE scope='owner' ORDER BY full_name LIMIT 100",
    },
    {
        "id": "files", "title": "Find captured RAPP paths",
        "sql": "SELECT r.full_name, f.path, f.path_bytes_base64, f.mode, f.git_object "
               "FROM files f JOIN repos r USING(generation_id,unit_id) "
               "WHERE f.path LIKE '%rapp%' ORDER BY r.full_name, f.path LIMIT 100",
    },
    {
        "id": "blobs", "title": "Most shared captured blobs",
        "sql": "SELECT git_object, sha256, bytes, reference_count, content_status "
               "FROM blobs ORDER BY reference_count DESC, git_object LIMIT 50",
    },
    {
        "id": "gaps", "title": "Unavailable gitlinks (not silently omitted)",
        "sql": 'SELECT r.full_name, d.path, d."commit", d.status FROM dependency_links d '
               "JOIN repos r ON r.generation_id=d.generation_id AND r.unit_id=d.parent_unit_id "
               "WHERE d.status='unavailable' ORDER BY r.full_name, d.path",
    },
    {
        "id": "protocols", "title": "Legacy declarations and RAPP1 measurements",
        "sql": "SELECT status, sum(count) AS occurrences FROM status_counts "
               "WHERE category='artifact_declaration' GROUP BY status ORDER BY status",
    },
    {
        "id": "changes", "title": "Semantic public-source change history",
        "sql": "SELECT c.sequence, e.observed_at, c.repository_id, c.kind, c.after_sha256 "
               "FROM change_observations c JOIN observation_events e USING(sequence) "
               "ORDER BY c.sequence DESC, c.repository_id LIMIT 100",
    },
    {
        "id": "payload-boundary", "title": "Complete metadata versus withheld raw payload",
        "sql": "SELECT * FROM metadata_payload_boundaries ORDER BY generation_id",
    },
    {
        "id": "measured-workflows", "title": "Bounded reported workflow observations",
        "sql": "SELECT observation_id,observed_date,result,producer,receiver,source_commit,"
               "source_bytes,reporting_basis FROM workflow_observations ORDER BY observed_date,observation_id",
    },
    {
        "id": "workflow-scope", "title": "Workflow limits: not global runtime or authority certification",
        "sql": "SELECT observation_id,category,name,value FROM workflow_measurements "
               "WHERE category IN ('scope','reference') ORDER BY observation_id,category,name",
    },
    {
        "id": "workflow-integration", "title": "Source integration milestones, distinct from tested commits",
        "sql": "SELECT observation_id,tested_source_commit,merge_commit,target_branch,state,"
               "reference_protocol_ratified FROM workflow_integrations ORDER BY observation_id",
    },
]


def validate(root):
    root = Path(root)
    data = root / "data"
    require(data.is_dir() and not data.is_symlink(), "invalid_data_directory")
    require({path.name for path in data.iterdir()} ==
            {"authority.json", "distribution.json", "payload-policy.json", "generations",
             "observations", "workflows", "workflow-integrations.json"},
            "unlisted_public_input")
    total = 0
    for path in data.rglob("*"):
        require(not path.is_symlink(), "symlink_input")
        if path.is_file():
            require(path.suffix in {".json", ".jsonl"} or path.name == ".gitkeep",
                    "nonmetadata_git_input")
            require(path.stat().st_size < 50 * 1024 * 1024, "oversized_git_input")
            total += path.stat().st_size
    require(total < 100 * 1024 * 1024, "metadata_git_budget_exceeded")
    config = read_config(root)
    generations = read_generations(root)
    authority = read_authority(root)
    distribution = read_distribution(root, config)
    known = {manifest["generation_id"] for manifest, _ in generations}
    require(all(item["generation_id"] in known for item in distribution["generations"]),
            "unindexed_distribution")
    baseline = initial_state(root, generations)
    state, events = replay_events(root, baseline)
    workflows = read_workflows(root)
    integrations = read_integrations(root, workflows)
    return {
        "config": config, "generations": generations, "authority": authority,
        "distribution": distribution, "baseline": baseline, "state": state, "events": events,
        "workflows": workflows,
        "integrations": integrations,
        "metadata_bytes": total,
    }


def _text(value):
    return canonical(value).decode("ascii").rstrip("\n")


def write_database(path, bundle):
    db = sqlite3.connect(path)
    try:
        db.executescript((Path(__file__).parent / "schema.sql").read_text())
        for manifest, tables in bundle["generations"]:
            gen = manifest["generation_id"]
            db.execute("INSERT INTO generations VALUES(?,?,?,?,?)", (
                gen, manifest["parent_generation_id"], manifest["captured_at"],
                manifest["inventory_status"], manifest["runtime_compatibility"],
            ))
            db.executemany("INSERT INTO source_locks VALUES(?,?,?)", (
                (gen, key, value) for key, value in sorted(manifest["provenance"].items())
            ))
            for table, columns in COLUMNS.items():
                placeholders = ",".join("?" for _ in range(len(columns) + 1))
                db.executemany(f"INSERT INTO {table} VALUES({placeholders})",
                               ([gen, *row] for row in tables[table]))
        for role in ("accepted", "content_source", "candidate"):
            item = bundle["authority"][role]
            db.execute("INSERT INTO authority_context VALUES(?,?,?,?,?,?,?,?)", (
                role, item["status"], item["commit"], item.get("revision"),
                item.get("repository_url") or item.get("pull_request_url"),
                item.get("pull_request_number"),
                item.get("spec_path"), item.get("spec_sha256"),
            ))
        for item in bundle["distribution"]["generations"]:
            db.execute("INSERT INTO distributions VALUES(?,?,?,?,?,?,?,?)", (
                item["generation_id"], item["carrier_bytes"], item["carrier_sha256"],
                item["frame_hash"], item["publication_status"], item["screening_status"],
                item["clear_for_exact_publication"],
                _text(item["verification"]) if item["verification"] else None,
            ))
            db.executemany("INSERT INTO release_parts VALUES(?,?,?,?,?,?)", (
                (item["generation_id"], part["index"], part["offset"], part["bytes"], part["sha256"], part["url"])
                for part in item["parts"]
            ))
        old_state = dict(bundle["baseline"])
        changed_at = {}
        for event in bundle["events"]:
            db.execute("INSERT INTO observation_events VALUES(?,?,?,?,?)", (
                event["sequence"], event["source"], event["observed_at"],
                event["previous_sha256"], event["snapshot_sha256"],
            ))
            for change in event["changes"]:
                row = change["after"]
                previous = old_state.get(row["repository_id"])
                if previous is None:
                    kind = "discovered_public"
                elif row["availability"] == "not_listed_public":
                    kind = "not_listed_public"
                elif row["full_name"] != previous["full_name"]:
                    kind = "renamed_public"
                elif previous["availability"] == "not_polled":
                    kind = "first_public_observation"
                elif previous["availability"] == "not_listed_public":
                    kind = "publicly_listed_again"
                else:
                    kind = "source_changed"
                db.execute("INSERT INTO change_observations VALUES(?,?,?,?,?,?)", (
                    event["sequence"], row["repository_id"], change["before_sha256"],
                    digest(row), kind, _text(row),
                ))
                changed_at[row["repository_id"]] = (event["observed_at"], event["sequence"])
                old_state[row["repository_id"]] = row
        require(old_state == bundle["state"], "database_history_replay_mismatch")
        for row in state_rows(bundle["state"]):
            when, sequence = changed_at.get(row["repository_id"], (None, None))
            db.execute("INSERT INTO observed_repos VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                row["repository_id"], row["full_name"], row["public_url"], row["default_branch"],
                row["observed_head"], row["availability"], _text(row["previous_names"]),
                row["archived"], row["fork"], row["license_spdx"], when, sequence,
            ))
        placeholders = ",".join("?" for _ in OBSERVATION_COLUMNS)
        for observation in bundle["workflows"]:
            db.execute(f"INSERT INTO workflow_observations VALUES({placeholders})",
                       observation_row(observation))
            db.executemany("INSERT INTO workflow_measurements VALUES(?,?,?,?)",
                           measurement_rows(observation))
        placeholders = ",".join("?" for _ in INTEGRATION_COLUMNS)
        db.executemany(f"INSERT INTO workflow_integrations VALUES({placeholders})",
                       ([row[key] for key in INTEGRATION_COLUMNS] for row in bundle["integrations"]))
        db.commit()
        require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "sqlite_integrity_failed")
        require(db.execute("PRAGMA foreign_key_check").fetchall() == [], "sqlite_foreign_key_failed")
        db.row_factory = sqlite3.Row
        summaries = [dict(row) for row in db.execute("SELECT * FROM catalog_summary ORDER BY generation_id")]
        for summary, (manifest, _) in zip(summaries, bundle["generations"], strict=True):
            require({key: summary[key] for key in manifest["counts"]} == manifest["counts"],
                    "sqlite_projection_count_mismatch")
        for preset in PRESETS:
            db.execute(preset["sql"]).fetchall()
        return summaries
    finally:
        db.close()


def freshness_state(receipt, now, stale_after):
    result = {"poll_status": "unknown", "freshness": "unknown", "last_success_at": None}
    if receipt is None or receipt.get("status") == "unknown":
        return result
    result["poll_status"] = receipt["status"]
    last = receipt.get("last_success_at")
    if last is not None:
        age = (dt.datetime.fromisoformat(timestamp(now).replace("Z", "+00:00")) -
               dt.datetime.fromisoformat(timestamp(last).replace("Z", "+00:00"))).total_seconds()
        result["last_success_at"] = timestamp(last)
        result["freshness"] = "unknown" if age < 0 else ("stale" if age > stale_after else "fresh")
    return result


def read_freshness(path, snapshot, config):
    if path is None:
        return {
            "schema": "rapp-public-freshness/1", "status": "unknown",
            "checked_at": None, "last_success_at": None, "snapshot_sha256": snapshot,
            "semantic_changes": 0, "requests": 0, "error": None,
            "stale_after_seconds": config["stale_after_seconds"],
            "head_recheck_max_seconds": 86400,
        }
    value = load_json(path)
    exact_keys(value, {"schema", "status", "checked_at", "last_success_at", "snapshot_sha256",
                       "semantic_changes", "requests", "error"})
    require(value["schema"] == "rapp-public-poll-receipt/1", "freshness_receipt_schema")
    enum(value["status"], {"success", "failed"})
    timestamp(value["checked_at"])
    if value["last_success_at"] is not None:
        timestamp(value["last_success_at"])
        require(value["last_success_at"] <= value["checked_at"], "freshness_clock_regression")
    require(hex_id(value["snapshot_sha256"], 64) == snapshot, "freshness_snapshot_mismatch")
    integer(value["semantic_changes"])
    integer(value["requests"])
    if value["status"] == "success":
        require(value["last_success_at"] == value["checked_at"] and value["error"] is None,
                "invalid_success_receipt")
    else:
        import re
        require(isinstance(value["error"], str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value["error"]), "invalid_failure_receipt")
    safe_tree(value)
    return {**value, "schema": "rapp-public-freshness/1",
            "stale_after_seconds": config["stale_after_seconds"], "head_recheck_max_seconds": 86400}


def spreadsheet_cell(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def write_csv(path, columns, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([spreadsheet_cell(value) for value in row] for row in rows)


def build(root, output="site", freshness=None):
    import re
    root = Path(root)
    output = Path(output)
    require(not output.is_symlink() and output.resolve().parent == root.resolve()
            and re.fullmatch(r"site(?:-[a-z0-9]+)*", output.name),
            "unsafe_build_destination")
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = validate(root)
    config = bundle["config"]
    snapshot = state_digest(bundle["state"])
    receipt = read_freshness(freshness, snapshot, config)
    with workspace(root, "build") as work:
        stage = work / "site"
        stage.mkdir()
        db_path = stage / "catalog.sqlite"
        summaries = write_database(db_path, bundle)
        web = root / "web"
        require(web.is_dir() and not web.is_symlink()
                and {path.name for path in web.iterdir()} == {"index.html", "style.css", "app.js"},
                "unlisted_web_input")
        for path in web.iterdir():
            require(path.is_file() and not path.is_symlink(), "symlink_input")
            shutil.copyfile(path, stage / path.name)
        shutil.copytree(root / "data", stage / "data", ignore=shutil.ignore_patterns(".gitkeep"))
        shutil.copyfile(Path(__file__).parent / "schema.sql", stage / "schema.sql")
        for name in ("llms.txt", "README.md", "LICENSE", "DATA_LICENSES.md", "DISTRIBUTION.md"):
            require(not (root / name).is_symlink(), "symlink_input")
            shutil.copyfile(root / name, stage / name)
        presets = []
        base = "https://lite.datasette.io/?" + urllib.parse.urlencode({
            "url": config["site_url"] + "catalog.sqlite",
        })
        for preset in PRESETS:
            presets.append({**preset, "url": base + "#/catalog?" +
                            urllib.parse.urlencode({"sql": preset["sql"]})})
        all_repos = []
        csv_repos = []
        csv_statuses = []
        for manifest, tables in bundle["generations"]:
            for row in tables["repos"]:
                record = dict(zip(COLUMNS["repos"], row, strict=True))
                record["generation_id"] = manifest["generation_id"]
                observed = bundle["state"].get(record["repository_id"])
                record["observation"] = observed
                all_repos.append(record)
                csv_repos.append([manifest["generation_id"], *row])
            csv_statuses.extend([manifest["generation_id"], *row] for row in tables["status_counts"])
        db_bytes, db_sha = db_path.stat().st_size, file_hash(db_path)
        payload_boundaries = []
        generation_manifests = {manifest["generation_id"]: manifest for manifest, _ in bundle["generations"]}
        for item in bundle["distribution"]["generations"]:
            payload_boundaries.append({
                "generation_id": item["generation_id"], "projection_of": item["carrier_sha256"],
                "metadata_coverage": generation_manifests[item["generation_id"]]["inventory_status"],
                "projection_kind": "allowlisted_metadata_only", "raw_payload_included": False,
                "payload_availability": item["publication_status"],
                "clear_for_exact_publication": item["clear_for_exact_publication"],
                "encrypted_full_upload_allowed": False,
            })
        latest_payload = next((item for item in payload_boundaries
                               if item["generation_id"] == summaries[-1]["generation_id"]), None)
        machine_index = {
            "schema": "rapp-public-catalog-index/1",
            "product": "RAPP",
            "repository_url": "https://github.com/" + config["observer_repository"],
            "site_url": config["site_url"],
            "snapshot_sha256": snapshot,
            "projection_kind": "allowlisted_metadata_only",
            "projection_of": latest_payload["projection_of"] if latest_payload else None,
            "raw_payload_included": False,
            "payload_availability": latest_payload["payload_availability"] if latest_payload else "not_recorded",
            "clear_for_exact_publication": latest_payload["clear_for_exact_publication"] if latest_payload else False,
            "encrypted_full_upload_allowed": False,
            "database": {"path": "catalog.sqlite", "bytes": db_bytes, "sha256": db_sha,
                         "read_only": True, "server_sql_api": False},
            "endpoints": {
                "repositories": "repos.json", "observed_repositories": "observed-repos.json",
                "generations": "generations.json", "freshness": "freshness.json",
                "authority": "data/authority.json", "distribution": "data/distribution.json",
                "distribution_guide": "DISTRIBUTION.md",
                "payload_policy": "data/payload-policy.json", "payload_boundaries": "payload-boundaries.json",
                "workflow_observations": "workflow-observations.json",
                "workflow_integrations": "data/workflow-integrations.json",
                "queries": "queries.json", "agent": "llms.txt", "schema": "schema.sql",
                "repository_csv": "repos.csv", "status_csv": "status-counts.csv",
                "artifact_manifest": "artifact-manifest.json",
            },
            "generations": summaries,
            "metadata_bytes": bundle["metadata_bytes"],
            "observation_events": len(bundle["events"]),
            "observed_repository_rows": len(bundle["state"]),
            "measured_workflow_observations": len(bundle["workflows"]),
        }
        outputs = {
            "index.json": machine_index,
            "repos.json": {"schema": "rapp-public-repos/1", "rows": all_repos},
            "observed-repos.json": {"schema": "rapp-public-observed-repos/1",
                                    "snapshot_sha256": snapshot, "rows": state_rows(bundle["state"])},
            "generations.json": {"schema": "rapp-public-generations/1",
                                 "rows": [manifest for manifest, _ in bundle["generations"]]},
            "queries.json": {"schema": "rapp-public-queries/1", "datasette_lite": base,
                              "server_sql_api": False, "queries": presets},
            "freshness.json": receipt,
            "payload-boundaries.json": {"schema": "rapp-public-payload-boundaries/1",
                                        "rows": payload_boundaries},
            "workflow-observations.json": {"schema": "rapp-public-workflow-observations/1",
                                           "reporting_basis": "public_safe_independent_summaries",
                                           "rows": bundle["workflows"],
                                           "source_integrations": bundle["integrations"]},
        }
        for name, value in outputs.items():
            safe_tree(value)
            (stage / name).write_bytes(canonical(value))
        write_csv(stage / "repos.csv", ["generation_id", *COLUMNS["repos"]], csv_repos)
        write_csv(stage / "status-counts.csv", ["generation_id", *COLUMNS["status_counts"]], csv_statuses)
        (stage / ".nojekyll").write_bytes(b"")
        artifacts = []
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                artifacts.append({"path": path.relative_to(stage).as_posix(),
                                  "bytes": path.stat().st_size, "sha256": file_hash(path)})
        artifact_manifest = {
            "schema": "rapp-public-build-artifacts/1", "snapshot_sha256": snapshot,
            "sqlite_version": sqlite3.sqlite_version, "artifacts": artifacts,
        }
        (stage / "artifact-manifest.json").write_bytes(canonical(artifact_manifest))
        sums = "".join(f"{item['sha256']}  {item['path']}\n" for item in artifacts)
        sums += f"{file_hash(stage / 'artifact-manifest.json')}  artifact-manifest.json\n"
        (stage / "SHA256SUMS").write_text(sums, encoding="ascii")
        pages_bytes = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file())
        require(pages_bytes < 750 * 1024 * 1024, "pages_budget_exceeded")
        result = {
            "database_bytes": db_bytes, "database_sha256": db_sha,
            "metadata_bytes": bundle["metadata_bytes"], "pages_bytes": pages_bytes,
            "sqlite_version": sqlite3.sqlite_version, "snapshot_sha256": snapshot,
            "artifact_manifest_sha256": file_hash(stage / "artifact-manifest.json"),
            "coverage": summaries,
        }
        replace_directory(stage, output)
    return result


def query(database, sql, limit=1000):
    require(0 < limit <= 1000000, "invalid_query_limit")
    require(isinstance(sql, str) and len(sql) < 100000, "invalid_query")
    path = Path(database).resolve()
    require(path.is_file(), "database_missing")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_RECURSIVE}
        connection.set_authorizer(
            lambda action, _arg1, _arg2, _db, _source:
            sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1
            return int(ticks > 10000)

        connection.set_progress_handler(progress, 10000)
        cursor = connection.execute(sql)
        require(cursor.description is not None, "query_must_return_rows")
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(limit + 1)
        require(len(rows) <= limit, "query_result_exceeds_limit")
        return {"columns": columns, "rows": rows}
    except sqlite3.Error as error:
        raise CatalogError("read_only_query_failed") from error
    finally:
        connection.close()
