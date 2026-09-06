import importlib.util
import io
from pathlib import Path
import re
from types import SimpleNamespace
from unittest import mock

from rapp_catalog.common import CatalogError, canonical, load_json
from rapp_catalog.distribution import descriptor_hash, read_authority, read_distribution, verify_distribution
from tests.helpers import CatalogCase, ROOT


class DistributionTests(CatalogCase):
    def test_content_source_binding_rejects_unsafe_or_inconsistent_metadata(self):
        path = self.root / "data" / "authority.json"
        original = load_json(path)
        for key, value in (("spec_path", "../SPEC.md"), ("spec_sha256", "not-a-hash"),
                           ("revision", "rev-99"), ("repository_url", "https://github.com/other/source")):
            with self.subTest(field=key):
                modified = load_json(path)
                modified["content_source"][key] = value
                path.write_bytes(canonical(modified))
                with self.assertRaises(CatalogError):
                    read_authority(self.root)
                path.write_bytes(canonical(original))

    def manifest(self):
        return load_json(self.root / "data" / "distribution.json")

    def write_manifest(self, manifest):
        (self.root / "data" / "distribution.json").write_bytes(canonical(manifest))

    def test_unpublished_carrier_cannot_be_retrieved(self):
        config = load_json(self.root / "catalog.json")
        with self.assertRaisesRegex(CatalogError, "carrier_not_publicly_published"):
            verify_distribution(self.root, config, "g0002")

    def test_invented_published_status_without_screening_or_urls_fails(self):
        config = load_json(self.root / "catalog.json")
        value = self.manifest()
        value["generations"][0]["publication_status"] = "published"
        self.write_manifest(value)
        with self.assertRaises(CatalogError):
            read_distribution(self.root, config)

    def test_release_assets_must_be_smaller_than_two_gib_and_version_specific(self):
        config = load_json(self.root / "catalog.json")
        value = self.manifest()
        carrier = value["generations"][0]
        carrier.update(publication_status="staged", screening_status="approved",
                       clear_for_exact_publication=True, carrier_bytes=2 ** 31)
        carrier["parts"] = [{"index": 1, "offset": 0, "bytes": 2 ** 31, "sha256": "a" * 64,
                             "url": "https://github.com/example/catalog/releases/download/g0002/part-001"}]
        self.write_manifest(value)
        with self.assertRaisesRegex(CatalogError, "oversized_release_asset"):
            read_distribution(self.root, config)
        carrier["carrier_bytes"] = carrier["parts"][0]["bytes"] = 100
        carrier["parts"][0]["url"] = "https://github.com/example/catalog/releases/latest/download/part-001"
        self.write_manifest(value)
        with self.assertRaisesRegex(CatalogError, "invalid_release_asset_url"):
            read_distribution(self.root, config)

    def test_publication_proof_is_bound_to_actual_parts_and_assembled_hash(self):
        import hashlib
        config = load_json(self.root / "catalog.json")
        value = self.manifest()
        item = value["generations"][0]
        bodies = [b"public first part\n", b"public second part\n"]
        full = b"".join(bodies)
        item.update(publication_status="staged", screening_status="approved", clear_for_exact_publication=True,
                    carrier_bytes=len(full), carrier_sha256=hashlib.sha256(full).hexdigest())
        item["parts"] = [
            {"index": i, "offset": sum(len(b) for b in bodies[:i - 1]),
             "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
             "url": f"https://github.com/example/catalog/releases/download/g0002/part-{i:03d}"}
            for i, body in enumerate(bodies, 1)
        ]
        self.write_manifest(value)

        def download(request, timeout):
            result = io.BytesIO(bodies[int(request.full_url[-3:]) - 1])
            result.status = 200
            result.headers = {}
            return result

        opener = SimpleNamespace(open=download)
        with mock.patch("rapp_catalog.distribution.urllib.request.build_opener", return_value=opener):
            proof = verify_distribution(self.root, config, "g0002")
        self.assertEqual(proof["descriptor_sha256"], descriptor_hash(item))
        self.assertEqual(proof["carrier_sha256"], hashlib.sha256(full).hexdigest())
        item.update(publication_status="published", verification=proof)
        self.write_manifest(value)
        read_distribution(self.root, config)
        item["parts"][0]["sha256"] = "0" * 64
        self.write_manifest(value)
        with self.assertRaisesRegex(CatalogError, "distribution_proof_mismatch"):
            read_distribution(self.root, config)

    def test_bad_chunk_hash_cannot_generate_verification_receipt(self):
        import hashlib
        config = load_json(self.root / "catalog.json")
        value = self.manifest()
        item = value["generations"][0]
        item.update(publication_status="staged", screening_status="approved", clear_for_exact_publication=True,
                    carrier_bytes=3, carrier_sha256=hashlib.sha256(b"abc").hexdigest())
        item["parts"] = [{"index": 1, "offset": 0, "bytes": 3, "sha256": "0" * 64,
                          "url": "https://github.com/example/catalog/releases/download/g0002/part-001"}]
        self.write_manifest(value)
        response = io.BytesIO(b"abc")
        response.status = 200
        response.headers = {}
        opener = SimpleNamespace(open=lambda *_args, **_kwargs: response)
        with mock.patch("rapp_catalog.distribution.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(CatalogError, "release_asset_integrity_mismatch"):
                verify_distribution(self.root, config, "g0002")
        self.assertIsNone(self.manifest()["generations"][0]["verification"])


class WorkflowTests(CatalogCase):
    def helper(self):
        spec = importlib.util.spec_from_file_location("commit_helper", ROOT / "scripts" / "commit_observations.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fake_git(self, failure=None, changes=False):
        calls = []

        def run(args, **_kwargs):
            calls.append(args)
            if args[1] == "status":
                output = b"?? data/observations/0000000001.json\0" if changes else b""
                return SimpleNamespace(returncode=0, stdout=output, stderr=b"")
            code = failure[1] if failure and args[1] == failure[0] else (1 if changes and args[1] == "diff" else 0)
            return SimpleNamespace(returncode=code, stdout=b"", stderr=b"")

        return calls, run

    def test_clean_git_diff_is_successful_noop_without_commit_or_push(self):
        helper = self.helper()
        calls, run = self.fake_git()
        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.commit_observations(), {"semantic_commit": False})
        self.assertFalse(any(args[1] in {"commit", "push"} for args in calls))

    def test_real_diff_error_is_not_mistaken_for_noop(self):
        helper = self.helper()
        calls, run = self.fake_git(("diff", 2))
        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            with self.assertRaises(helper.GitFailure):
                helper.commit_observations()
        self.assertFalse(any(args[1] == "commit" for args in calls))

    def test_commit_and_push_errors_fail_without_broad_success_fallback(self):
        for operation in ("commit", "push"):
            with self.subTest(operation=operation):
                helper = self.helper()
                calls, run = self.fake_git((operation, 1), changes=True)
                with mock.patch.object(helper.subprocess, "run", side_effect=run):
                    with self.assertRaises(helper.GitFailure):
                        helper.commit_observations()
                if operation == "commit":
                    self.assertFalse(any(args[1] == "push" for args in calls))

    def test_only_new_semantic_event_files_can_be_committed(self):
        helper = self.helper()
        result = SimpleNamespace(returncode=0, stdout=b" M data/distribution.json\0", stderr=b"")
        with mock.patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(helper.GitFailure, "unexpected_worktree_change"):
                helper.commit_observations()

    def test_changed_event_is_staged_committed_and_pushed_exactly_once(self):
        helper = self.helper()
        calls, run = self.fake_git(changes=True)
        with mock.patch.object(helper.subprocess, "run", side_effect=run):
            self.assertEqual(helper.commit_observations(), {"semantic_commit": True})
        self.assertEqual(sum(args[1] == "commit" for args in calls), 1)
        self.assertEqual(sum(args[1] == "push" for args in calls), 1)
        self.assertIn(["git", "push", "origin", "HEAD:main"], calls)

    def test_workflow_permissions_pins_schedule_and_timeouts(self):
        workflow = (ROOT / ".github" / "workflows" / "catalog.yml").read_text()
        self.assertIn("17 * * * *", workflow)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertEqual(workflow.count("pages: write"), 1)
        self.assertEqual(workflow.count("id-token: write"), 1)
        self.assertEqual(workflow.count("timeout-minutes:"), 3)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertNotIn("pull_request_target", workflow)
        actions = re.findall(r"uses:\s+(\S+)", workflow)
        self.assertTrue(actions)
        self.assertTrue(all(re.fullmatch(r"actions/[a-z/-]+@[0-9a-f]{40}", action) for action in actions))
        self.assertNotIn("|| true", workflow)
        self.assertNotIn("git commit ||", workflow)
