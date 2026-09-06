import copy

from rapp_catalog.build import build, query
from rapp_catalog.common import CatalogError, canonical, file_hash, load_json
from rapp_catalog.workflows import read_integrations, read_workflows, validate_workflow
from tests.helpers import CatalogCase, ROOT


class WorkflowObservationTests(CatalogCase):
    def setUp(self):
        super().setUp()
        self.path = self.root / "data" / "workflows" / "2026-09-06-dogg-public-bridge.json"
        self.record = load_json(self.path)

    def test_reported_measurements_and_identity_are_preserved_exactly(self):
        records = read_workflows(self.root)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["source"]["commit"], "7fce94a13f1537e214c6c612d085ac344679bbd1")
        self.assertEqual(record["source"]["bytes"], 551)
        self.assertEqual(record["source"]["sha256"],
                         "9855a8f61afb26da5a93fcac7cb13e6e54c5c4a39f2240e120b90d65aa69a81c")
        self.assertEqual(record["measured"]["producer"], "CPython 3.14.4")
        self.assertEqual(record["measured"]["receiver"], "Node.js 25.2.0")
        self.assertEqual(record["measured"]["emitted_frames_verified"], 1)
        self.assertEqual(record["measured"]["bridge_contracts_passed"], 27)
        self.assertEqual(record["scope"]["physical_machine_count"], 1)
        self.assertEqual(record["source_summary_sha256"],
                         "ffe70bfcf2ed2b3c5bdd58f28ec2be0b88755036ebcb5b8141df3d28e234beff")

    def test_scope_cannot_widen_into_global_runtime_or_authority_claims(self):
        changes = [
            ("scope", "physical_machine_count", 2),
            ("scope", "different_physical_machines", True),
            ("scope", "os_enforced_sandbox", True),
            ("scope", "authenticated_consumer_acceptance", True),
            ("scope", "full_organism_runtime_compatibility", True),
            ("scope", "historical_native_records_rewritten", True),
            ("reference_candidate", "accepted_new_protocol_revision", True),
            ("measured", "private_bootstrap_or_prior_context", True),
            ("measured", "source_repairs", 1),
            ("measured", "recovered_code_executed", True),
            ("measured", "byte_comparison_exit_code", 1),
        ]
        for group, key, value in changes:
            with self.subTest(field=key):
                record = copy.deepcopy(self.record)
                record[group][key] = value
                with self.assertRaises(CatalogError):
                    validate_workflow(record)

    def test_unexamined_fields_private_paths_and_oversized_counts_fail(self):
        changes = [
            lambda row: row.update(raw_execution_log="not an allowed field"),
            lambda row: row["source"].update(path="../private"),
            lambda row: row["source"].update(path="/" + "Users/synthetic/local"),
            lambda row: row.update(public_starting_point="file" + "://local"),
            lambda row: row["source"].update(bytes=2 ** 64),
            lambda row: row["measured"].update(native_frames_checked=2 ** 64),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                record = copy.deepcopy(self.record)
                change(record)
                with self.assertRaises(CatalogError):
                    validate_workflow(record)

    def test_workflow_is_queryable_separately_from_head_history_and_authority(self):
        before_generation = file_hash(self.root / "data" / "generations" / "g0002" / "manifest.json")
        before_authority = file_hash(self.root / "data" / "authority.json")
        build(self.root, self.root / "site")
        database = self.root / "site" / "catalog.sqlite"
        rows = query(database, "SELECT producer,receiver,source_bytes,reporting_basis FROM workflow_observations")
        self.assertEqual(rows["rows"], [("CPython 3.14.4", "Node.js 25.2.0", 551,
                                        "public_safe_independent_summary_only")])
        flags = dict(query(database, "SELECT name,value FROM workflow_measurements WHERE category='scope'")["rows"])
        self.assertEqual(flags["physical_machine_count"], 1)
        self.assertEqual(flags["unsigned_local_snapshot_bridge"], 1)
        self.assertEqual(flags["full_organism_runtime_compatibility"], 0)
        self.assertEqual(flags["authenticated_consumer_acceptance"], 0)
        self.assertEqual(query(database, "SELECT count(*) FROM observation_events")["rows"], [(0,)])
        self.assertEqual(query(database, "SELECT runtime_compatibility FROM generations")["rows"],
                         [("not_established",)])
        self.assertEqual(file_hash(self.root / "data" / "authority.json"), before_authority)
        self.assertEqual(file_hash(self.root / "data" / "generations" / "g0002" / "manifest.json"), before_generation)
        exported = load_json(self.root / "site" / "workflow-observations.json")
        self.assertEqual(exported["rows"], [self.record])
        self.assertFalse(exported["rows"][0]["reference_candidate"]["accepted_new_protocol_revision"])

    def test_bad_workflow_input_preserves_last_good_site(self):
        build(self.root, self.root / "site")
        before = file_hash(self.root / "site" / "catalog.sqlite")
        self.record["scope"]["full_organism_runtime_compatibility"] = True
        self.path.write_bytes(canonical(self.record))
        with self.assertRaises(CatalogError):
            build(self.root, self.root / "site")
        self.assertEqual(file_hash(self.root / "site" / "catalog.sqlite"), before)

    def test_actual_withheld_payload_policy_is_not_changed_by_successful_bridge(self):
        record = read_workflows(ROOT)[0]
        distribution = load_json(ROOT / "data" / "distribution.json")["generations"][0]
        self.assertEqual(record["result"], "success-within-declared-scope")
        self.assertEqual(distribution["publication_status"], "withheld")
        self.assertFalse(distribution["clear_for_exact_publication"])
        self.assertEqual(distribution["parts"], [])

    def test_source_merge_is_separate_from_tested_commit_and_reference_candidate(self):
        before = file_hash(self.path)
        build(self.root, self.root / "site")
        result = query(self.root / "site" / "catalog.sqlite",
                       "SELECT tested_source_commit,merge_commit,target_branch,state,"
                       "reference_protocol_ratified FROM workflow_integrations")
        self.assertEqual(result["rows"], [
            ("7fce94a13f1537e214c6c612d085ac344679bbd1",
             "b4c45def3a5951eb15dc5a6eed7a1a7d20be68fe", "main", "merged", 0),
        ])
        self.assertEqual(file_hash(self.path), before)
        candidate = query(self.root / "site" / "catalog.sqlite",
                          "SELECT status FROM authority_context WHERE role='candidate'")
        self.assertEqual(candidate["rows"], [("reviewed_proposal_not_ratified",)])

    def test_integration_cannot_replace_tested_source_or_ratify_reference_protocol(self):
        path = self.root / "data" / "workflow-integrations.json"
        original = load_json(path)
        changes = [("tested_source_commit", "b4c45def3a5951eb15dc5a6eed7a1a7d20be68fe"),
                   ("reference_protocol_ratified", True),
                   ("repository", "https://github.com/other/source")]
        for key, value in changes:
            with self.subTest(field=key):
                altered = copy.deepcopy(original)
                altered["rows"][0][key] = value
                path.write_bytes(canonical(altered))
                with self.assertRaises(CatalogError):
                    read_integrations(self.root, [self.record])
        path.write_bytes(canonical(original))
