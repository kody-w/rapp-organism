import base64
from contextlib import closing
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3

from rapp_catalog.build import build, freshness_state, query, spreadsheet_cell, validate
from rapp_catalog.common import CatalogError, canonical, file_hash, load_json, path_fields
from rapp_catalog.model import COLUMNS, read_generation
from rapp_catalog.scrape import scrape
from rapp_catalog.seed import import_seed, make_projection
from tests.helpers import CatalogCase, FixtureClient, LATER, NOW, ROOT, XSS_PATH


class ProjectionTests(CatalogCase):
    def projection(self, values=None):
        values = values or self.inputs
        hashes = {
            "scope_sha256": "1" * 64, "genome_index_sha256": "2" * 64,
            "dependency_index_sha256": "3" * 64, "matrix_sha256": "4" * 64,
            "carrier_receipt_sha256": "5" * 64,
        }
        return make_projection(values["scope"], values["genome"], values["dependencies"],
                               values["matrix"], values["receipt"], "g0002", "g0001", hashes)

    def test_all_files_blobs_dependencies_and_empty_repositories_are_preserved(self):
        manifest, tables = self.projection()
        self.assertEqual(manifest["counts"], {
            "owner_repos": 2, "dependency_repos": 1, "owner_files": 6, "dependency_files": 1,
            "blobs": 2, "gitlinks": 2, "unavailable_gitlinks": 1, "source_blob_bytes": 12,
        })
        self.assertEqual(len(tables["files"]), 7)
        self.assertEqual(sum(row[6] for row in tables["blobs"]), 5)
        self.assertEqual(sum(row[10] is True for row in tables["repos"]), 1)
        self.assertEqual({row[5] for row in tables["dependency_links"]}, {"hydrated", "unavailable"})
        self.assertEqual({row[13] for row in tables["repos"]}, {"not_established"})

    def test_reimport_is_byte_identical_noop(self):
        before = self.public_hashes()
        result = import_seed(self.root, **self.paths)
        self.assertFalse(result["changed"])
        self.assertEqual(before, self.public_hashes())

    def test_conflicting_generation_cannot_overwrite_last_good(self):
        before = self.public_hashes()
        modified = copy.deepcopy(self.inputs["matrix"])
        modified["canonical"]["anchor_selected_commit"] = "9" * 40
        self.paths["matrix"].write_bytes(canonical(modified))
        with self.assertRaisesRegex(CatalogError, "immutable_generation_conflict"):
            import_seed(self.root, **self.paths)
        self.assertEqual(before, self.public_hashes())

    def test_reimport_does_not_ignore_unlisted_generation_directories(self):
        generation = self.root / "data" / "generations" / "g0002"
        (generation / "unexamined").mkdir()
        before = self.public_hashes()
        with self.assertRaises(CatalogError):
            import_seed(self.root, **self.paths)
        self.assertEqual(before, self.public_hashes())

    def test_locked_matrix_hash_is_enforced(self):
        before = self.public_hashes()
        with self.assertRaisesRegex(CatalogError, "unexpected_matrix_hash"):
            import_seed(self.root, **self.paths, expect_matrix_sha256="0" * 64)
        self.assertEqual(before, self.public_hashes())

    def test_private_scope_is_rejected(self):
        altered = copy.deepcopy(self.inputs)
        altered["scope"]["repositories"][0]["private"] = True
        altered["genome"]["source_scope"] = copy.deepcopy(altered["scope"])
        with self.assertRaisesRegex(CatalogError, "private_source"):
            self.projection(altered)

    def test_unexamined_strings_and_raw_analysis_are_not_projected(self):
        altered = copy.deepcopy(self.inputs)
        sensitive = "/" + "Users/synthetic/" + ".copilot/session-state/never-publish"
        altered["matrix"]["capture"] = {"root": sensitive}
        altered["matrix"]["blobs"][0]["analysis"] = {"raw": sensitive, "code": "do not copy source bodies"}
        altered["matrix"]["repositories"][0]["unmeasured"] = [{"diagnostic": sensitive}]
        manifest, tables = self.projection(altered)
        projected = canonical({"manifest": manifest, "tables": tables})
        self.assertNotIn(sensitive.encode(), projected)
        self.assertNotIn(b"do not copy source bodies", projected)
        self.assertNotIn(b"diagnostic", projected)

    def test_invalid_paths_fail_whole_projection_without_omission(self):
        paths = [
            b"/absolute", b"../escape", b"a/../escape", b"a//b", b"nul\0name",
            b"C:\\local\\capture", b"/" + b"Users/local/path",
            b".copilot/" + b"session-state/sensitive", b"ghp_" + b"A" * 36,
        ]
        before = self.public_hashes()
        for raw in paths:
            with self.subTest(pattern=raw[:10]):
                altered = copy.deepcopy(self.inputs)
                altered["genome"]["repositories"][0]["entries"][0]["path_bytes_base64"] = base64.b64encode(raw).decode()
                with self.assertRaises(CatalogError):
                    self.projection(altered)
                self.assertEqual(before, self.public_hashes())

    def test_relative_user_routes_and_non_utf8_names_are_lossless(self):
        raw = b"src/users/example.py"
        self.assertEqual(path_fields(base64.b64encode(raw).decode())[0], raw.decode())
        manifest, tables = self.projection()
        non_utf8 = [row for row in tables["files"] if row[1] is None]
        self.assertEqual(len(non_utf8), 1)
        self.assertEqual(base64.b64decode(non_utf8[0][2]), b"raw-\xff.txt")
        self.assertEqual(len(tables["files"]), manifest["counts"]["owner_files"] +
                         manifest["counts"]["dependency_files"])

    def test_missing_duplicate_or_unknown_source_rows_fail(self):
        mutations = [
            lambda x: x["genome"]["repositories"][0]["entries"].pop(),
            lambda x: x["matrix"]["blobs"].pop(),
            lambda x: x["matrix"]["repositories"].pop(),
            lambda x: x["matrix"]["blobs"][0].update(references=999),
            lambda x: x["matrix"]["blobs"][0].update(content_status="healthy"),
            lambda x: x["matrix"].update(report_status="partial"),
            lambda x: x["matrix"]["repositories"].append(copy.deepcopy(x["matrix"]["repositories"][0])),
        ]
        for change in mutations:
            with self.subTest(mutation=mutations.index(change)):
                altered = copy.deepcopy(self.inputs)
                change(altered)
                with self.assertRaises(CatalogError):
                    self.projection(altered)

    def test_every_public_row_has_exact_allowlisted_column_shape(self):
        manifest, tables = read_generation(self.root / "data" / "generations" / "g0002")
        for table, rows in tables.items():
            self.assertEqual(manifest["tables"][table]["columns"], COLUMNS[table])
            self.assertTrue(all(len(row) == len(COLUMNS[table]) for row in rows))
            for part in manifest["tables"][table]["partitions"]:
                self.assertLess(part["bytes"], 50 * 1024 * 1024)


class BuildTests(CatalogCase):
    def build(self, name="site", freshness=None):
        return build(self.root, self.root / name, freshness)

    def test_sqlite_schema_counts_and_bytes_are_reproducible(self):
        first = self.build()
        second = self.build("site-check")
        self.assertEqual(first, second)
        a = self.root / "site"
        b = self.root / "site-check"
        hashes = lambda root: {p.relative_to(root).as_posix(): file_hash(p)
                               for p in root.rglob("*") if p.is_file()}
        self.assertEqual(hashes(a), hashes(b))
        with closing(sqlite3.connect(a / "catalog.sqlite")) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            actual = db.execute("SELECT owner_repos,owner_files,dependency_repos,dependency_files,blobs,gitlinks,unavailable_gitlinks FROM catalog_summary").fetchone()
            self.assertEqual(actual, (2, 6, 1, 1, 2, 2, 1))
            self.assertEqual(db.execute("SELECT count(*) FROM files").fetchone(), (7,))
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE path IS NULL").fetchone(), (1,))
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE mode='120000'").fetchone(), (1,))

    def test_freshness_receipt_does_not_change_database(self):
        scrape(self.root, client=FixtureClient(), now=NOW)
        first = self.build()
        second = self.build("site-check", self.root / ".cache" / "last-run.json")
        self.assertEqual(first["database_sha256"], second["database_sha256"])
        self.assertNotEqual(first["artifact_manifest_sha256"], second["artifact_manifest_sha256"])
        self.assertEqual(load_json(self.root / "site" / "freshness.json")["status"], "unknown")
        self.assertEqual(load_json(self.root / "site-check" / "freshness.json")["status"], "success")

    def test_stale_unknown_failure_candidate_and_legacy_are_separate(self):
        self.assertEqual(freshness_state(None, NOW, 21600)["freshness"], "unknown")
        self.assertEqual(freshness_state({"status": "success", "last_success_at": NOW},
                                        LATER, 21600)["freshness"], "fresh")
        stale = freshness_state({"status": "failed", "last_success_at": NOW},
                                "2026-01-03T00:00:00Z", 21600)
        self.assertEqual(stale["poll_status"], "failed")
        self.assertEqual(stale["freshness"], "stale")
        self.build()
        with closing(sqlite3.connect(self.root / "site" / "catalog.sqlite")) as db:
            self.assertEqual(set(db.execute("SELECT role,status FROM authority_context")),
                             {("accepted", "accepted_canonical_checkpoint"),
                              ("content_source", "normative_content_source"),
                              ("candidate", "reviewed_proposal_not_ratified")})
            statuses = {row[0] for row in db.execute("SELECT status FROM status_counts WHERE category='artifact_declaration'")}
            self.assertEqual(statuses, {"declared_legacy_rapp:frame:refused",
                                       "declared_rapp1:frame:unverified_history"})
            self.assertEqual(db.execute("SELECT DISTINCT head_relation FROM repo_observations").fetchall(),
                             [("unknown",)])
            self.assertEqual(db.execute("SELECT publication_status,screening_status FROM distributions").fetchone(),
                             ("not_published", "pending"))

    def test_malicious_html_is_data_and_source_paths_are_never_extracted(self):
        self.build()
        db_path = self.root / "site" / "catalog.sqlite"
        result = query(db_path, "SELECT path FROM files WHERE path LIKE '%onerror%'")
        self.assertEqual(result["rows"], [(XSS_PATH,)])
        self.assertFalse((self.root / "site" / "src").exists())
        self.assertFalse((self.root / "site" / "link").exists())
        script = (self.root / "site" / "app.js").read_text()
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("document.write", script)
        self.assertIn("textContent", script)
        self.assertNotIn("<img", (self.root / "site" / "index.html").read_text())

    def test_read_only_queries_and_explicit_limits(self):
        self.build()
        database = self.root / "site" / "catalog.sqlite"
        before = file_hash(database)
        statements = [
            "DELETE FROM files", "DROP TABLE blobs", "PRAGMA user_version=99",
            "ATTACH DATABASE ':memory:' AS other",
            "SELECT load_extension('anything')",
        ]
        for statement in statements:
            with self.subTest(sql=statement):
                with self.assertRaises(CatalogError):
                    query(database, statement)
                self.assertEqual(file_hash(database), before)
        with self.assertRaisesRegex(CatalogError, "query_result_exceeds_limit"):
            query(database, "SELECT * FROM files", limit=1)
        self.assertEqual(query(database, "SELECT count(*) FROM files")["rows"], [(7,)])

    def test_invalid_source_and_unlisted_data_preserve_last_good_output(self):
        self.build()
        database = self.root / "site" / "catalog.sqlite"
        before = file_hash(database)
        extra = self.root / "data" / "unexamined.json"
        extra.write_bytes(b'{"private":"not permitted"}')
        with self.assertRaisesRegex(CatalogError, "unlisted_public_input"):
            self.build()
        self.assertEqual(file_hash(database), before)
        extra.unlink()
        partition = next((self.root / "data" / "generations" / "g0002").glob("files-*.jsonl"))
        partition.write_bytes(partition.read_bytes()[:-2])
        with self.assertRaises(CatalogError):
            self.build()
        self.assertEqual(file_hash(database), before)

    def test_output_cannot_overwrite_inputs_or_escape_root(self):
        for output in (self.root, self.root / "data", self.root / "data" / "site", self.work / "site"):
            with self.subTest(name=output.name):
                with self.assertRaisesRegex(CatalogError, "unsafe_build_destination"):
                    build(self.root, output)

    def test_symlinked_public_input_is_rejected_before_reading(self):
        path = self.root / "data" / "authority.json"
        path.unlink()
        path.symlink_to(self.input_dir / "matrix.json")
        with self.assertRaisesRegex(CatalogError, "symlink_input"):
            self.build()

    def test_tampered_event_chain_cannot_build(self):
        scrape(self.root, client=FixtureClient(), now=NOW)
        self.build()
        before = file_hash(self.root / "site" / "catalog.sqlite")
        event_path = self.root / "data" / "observations" / "0000000001.json"
        event = load_json(event_path)
        event["previous_sha256"] = "0" * 64
        event_path.write_bytes(canonical(event))
        with self.assertRaisesRegex(CatalogError, "observation_previous_hash_mismatch"):
            self.build()
        self.assertEqual(file_hash(self.root / "site" / "catalog.sqlite"), before)

    def test_checksum_manifest_covers_downloaded_products(self):
        self.build()
        site = self.root / "site"
        manifest = load_json(site / "artifact-manifest.json")
        for row in manifest["artifacts"]:
            self.assertEqual(file_hash(site / row["path"]), row["sha256"])
            self.assertEqual((site / row["path"]).stat().st_size, row["bytes"])
        self.assertIn("catalog.sqlite", {row["path"] for row in manifest["artifacts"]})
        self.assertIn("data/generations/g0002/manifest.json", {row["path"] for row in manifest["artifacts"]})
        self.assertFalse(load_json(site / "index.json")["database"]["server_sql_api"])

    def test_csv_formula_cells_are_explicitly_sanitized(self):
        self.assertEqual(spreadsheet_cell("=1+1"), "'=1+1")
        self.assertEqual(spreadsheet_cell("@SUM(1)"), "'@SUM(1)")
        self.assertEqual(spreadsheet_cell("example/alpha"), "example/alpha")

    def test_accessible_static_entrypoints_and_no_external_scripts(self):
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []

            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))

        parser = Tags()
        parser.feed((self.root / "web" / "index.html").read_text())
        labels = {attrs.get("for") for tag, attrs in parser.tags if tag == "label"}
        controls = {attrs["id"] for tag, attrs in parser.tags if tag in {"input", "select"}}
        self.assertTrue(controls <= labels)
        self.assertTrue(any(tag == "main" for tag, _ in parser.tags))
        self.assertTrue(any(tag == "noscript" for tag, _ in parser.tags))
        self.assertTrue(all(not attrs.get("src", "").startswith("http")
                            for tag, attrs in parser.tags if tag == "script"))
        self.assertTrue(any(tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
                            for tag, attrs in parser.tags))


class PublicSeedContractTests(CatalogCase):
    def test_authority_checkpoint_content_and_candidate_are_not_interchangeable(self):
        build(self.root, self.root / "site")
        result = query(self.root / "site" / "catalog.sqlite",
                       'SELECT role,"commit",spec_path,spec_sha256 FROM authority_context ORDER BY role')
        self.assertEqual(result["rows"], [
            ("accepted", "eb50008011447f5e69372ac22a1755f0978d15ed", None, None),
            ("candidate", "8a83b3fa8bebe16411fc4a130c6447ace15b43ac", None, None),
            ("content_source", "58058d08c9f0e340fae07c9865647f599c32634d", "SPEC.md",
             "348e7d5baa94aaf2ce4c5354f3cb261f389298a04af65e271a686d3b62f7c384"),
        ])
        with closing(sqlite3.connect(self.root / "site" / "catalog.sqlite")) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (6,))

    def test_checked_in_generation_has_full_required_scope(self):
        manifest = load_json(ROOT / "data" / "generations" / "g0002" / "manifest.json")
        self.assertEqual(manifest["counts"], {
            "owner_repos": 489, "dependency_repos": 3, "owner_files": 163303,
            "dependency_files": 2841, "blobs": 96781, "gitlinks": 15,
            "unavailable_gitlinks": 12, "source_blob_bytes": 6270141128,
        })
        self.assertEqual(manifest["provenance"]["matrix_sha256"],
                         "036237ffc6fe6527f466a3fe8407adcec70f3214a6caee5eb4aee07ca7ad4ddd")
        self.assertEqual(manifest["tables"]["files"]["rows"], 166144)
        self.assertEqual(manifest["tables"]["blobs"]["rows"], 96781)
