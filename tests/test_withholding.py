import contextlib
import copy
import hashlib
import io
import json
from unittest import mock

from rapp_catalog.__main__ import main
from rapp_catalog.build import build, query
from rapp_catalog.common import CatalogError, canonical, file_hash, load_json, read_config
from rapp_catalog.distribution import descriptor_hash, read_distribution, verify_distribution
from rapp_catalog.downloads import assemble_distribution, download_distribution, verify_carrier
from tests.helpers import CatalogCase, ROOT
from tests.test_downloads import Assets


class WithholdingTests(CatalogCase):
    def setUp(self):
        super().setUp()
        self.index_path = self.root / "data" / "distribution.json"
        self.policy_path = self.root / "data" / "payload-policy.json"
        self.index = load_json(self.index_path)
        self.item = self.index["generations"][0]
        self.item.update(publication_status="withheld", screening_status="blocked",
                         clear_for_exact_publication=False, parts=[], verification=None)
        self.index_path.write_bytes(canonical(self.index))
        self.policy = {
            "schema": "rapp-public-payload-policy/1", "encrypted_full_upload_allowed": False,
            "withheld_payloads": [{
                "generation_id": self.item["generation_id"],
                "projection_of": self.item["carrier_sha256"], "carrier_bytes": self.item["carrier_bytes"],
                "reason": "disclosure_and_rights_not_cleared",
            }],
        }
        self.policy_path.write_bytes(canonical(self.policy))

    def test_all_carrier_operations_refuse_before_network_or_payload_io(self):
        operations = (
            lambda: download_distribution(self.root),
            lambda: assemble_distribution(self.root),
            lambda: verify_distribution(self.root, read_config(self.root), "g0002"),
            lambda: verify_carrier(self.root, "does-not-exist.egg.zip"),
        )
        before = self.public_hashes()
        with mock.patch("rapp_catalog.distribution.urllib.request.build_opener",
                        side_effect=AssertionError("No network permitted")):
            for operation in operations:
                with self.subTest(operation=operations.index(operation)):
                    with self.assertRaisesRegex(CatalogError, "carrier_withheld"):
                        operation()
        self.assertFalse((self.root / "downloads").exists())
        self.assertEqual(before, self.public_hashes())

    def test_all_public_cli_paths_fail_with_explicit_withheld_error(self):
        commands = [
            ["download-distribution"], ["assemble-distribution"], ["verify-distribution"],
            ["verify-carrier", "--file", "unavailable.egg.zip"],
        ]
        with contextlib.chdir(self.root):
            for args in commands:
                with self.subTest(command=args[0]):
                    errors = io.StringIO()
                    with contextlib.redirect_stderr(errors):
                        self.assertEqual(main(args), 1)
                    self.assertEqual(json.loads(errors.getvalue()), {"error": "carrier_withheld"})
        self.assertFalse((self.root / "downloads").exists())

    def test_external_published_index_cannot_override_withholding(self):
        external = copy.deepcopy(self.index)
        item = external["generations"][0]
        item.update(publication_status="published", screening_status="approved",
                    clear_for_exact_publication=True)
        index = self.root / "external-index.json"
        index.write_bytes(canonical(external))
        with self.assertRaisesRegex(CatalogError, "withheld_payload_cannot_be_published"):
            download_distribution(self.root, manifest=index)
        self.assertFalse((self.root / "downloads").exists())

    def test_renaming_or_substituting_blocked_identity_is_not_a_workaround(self):
        for field, value in (("generation_id", "g0003"), ("carrier_sha256", "0" * 64),
                             ("carrier_bytes", 101)):
            with self.subTest(field=field):
                external = copy.deepcopy(self.index)
                external["generations"][0][field] = value
                external_path = self.root / "external-index.json"
                external_path.write_bytes(canonical(external))
                with self.assertRaisesRegex(CatalogError, "withheld_payload_identity_conflict"):
                    read_distribution(self.root, read_config(self.root), external_path)

    def test_withheld_record_cannot_be_removed_or_downgraded_to_pending(self):
        self.index["generations"] = []
        self.index_path.write_bytes(canonical(self.index))
        with self.assertRaisesRegex(CatalogError, "withheld_record_missing"):
            read_distribution(self.root, read_config(self.root))
        self.index["generations"] = [self.item]
        self.item.update(publication_status="not_published", screening_status="pending")
        self.index_path.write_bytes(canonical(self.index))
        with self.assertRaisesRegex(CatalogError, "withheld_payload_cannot_be_published"):
            read_distribution(self.root, read_config(self.root))

    def test_encrypted_full_policy_override_and_withheld_urls_are_rejected(self):
        self.policy["encrypted_full_upload_allowed"] = True
        self.policy_path.write_bytes(canonical(self.policy))
        with self.assertRaisesRegex(CatalogError, "encrypted_full_upload_forbidden"):
            read_distribution(self.root, read_config(self.root))
        self.policy["encrypted_full_upload_allowed"] = False
        self.policy_path.write_bytes(canonical(self.policy))
        self.item["parts"] = [{
            "index": 1, "offset": 0, "bytes": self.item["carrier_bytes"], "sha256": "1" * 64,
            "url": "https://github.com/example/catalog/releases/download/blocked/part-001",
        }]
        self.index_path.write_bytes(canonical(self.index))
        with self.assertRaises(CatalogError):
            read_distribution(self.root, read_config(self.root))

    def test_complete_metadata_remains_queryable_while_payload_is_withheld(self):
        generation = self.root / "data" / "generations" / "g0002" / "manifest.json"
        original_hash = file_hash(generation)
        build(self.root, self.root / "site")
        index = load_json(self.root / "site" / "index.json")
        self.assertEqual(index["projection_of"], self.item["carrier_sha256"])
        self.assertEqual(index["payload_availability"], "withheld")
        self.assertFalse(index["raw_payload_included"])
        self.assertFalse(index["clear_for_exact_publication"])
        self.assertFalse(index["encrypted_full_upload_allowed"])
        result = query(self.root / "site" / "catalog.sqlite",
                       "SELECT projection_of,metadata_coverage,payload_availability,raw_payload_included,"
                       "clear_for_exact_publication FROM metadata_payload_boundaries")
        self.assertEqual(result["rows"], [(self.item["carrier_sha256"], "complete_frozen_scope", "withheld", 0, 0)])
        self.assertEqual(query(self.root / "site" / "catalog.sqlite",
                               "SELECT count(*) FROM files")["rows"], [(7,)])
        self.assertEqual(query(self.root / "site" / "catalog.sqlite",
                               "SELECT count(*) FROM release_parts")["rows"], [(0,)])
        self.assertEqual(file_hash(generation), original_hash)

    def test_different_separately_cleared_future_identity_can_still_download(self):
        body = b"separately cleared synthetic future payload"
        item = {
            "generation_id": "g0003", "carrier_bytes": len(body),
            "carrier_sha256": hashlib.sha256(body).hexdigest(), "frame_hash": "3" * 64,
            "publication_status": "published", "screening_status": "approved",
            "clear_for_exact_publication": True,
            "parts": [{"index": 1, "offset": 0, "bytes": len(body),
                       "sha256": hashlib.sha256(body).hexdigest(),
                       "url": "https://github.com/example/catalog/releases/download/cleared/part-001"}],
            "verification": None,
        }
        item["verification"] = {
            "schema": "rapp-distribution-verification/1",
            "method": "downloaded_parts_and_assembled_sha256",
            "descriptor_sha256": descriptor_hash(item), "carrier_sha256": item["carrier_sha256"],
            "carrier_bytes": len(body), "verified_at": "2026-01-02T00:00:00Z",
        }
        external = self.root / "future-index.json"
        external.write_bytes(canonical({"schema": "rapp-public-distribution/3", "generations": [item]}))
        result = download_distribution(self.root, "g0003", manifest=external, opener=Assets([body]))
        self.assertEqual(result["status"], "parts_verified")
        self.assertEqual(result["generation_id"], "g0003")
        with self.assertRaisesRegex(CatalogError, "carrier_withheld"):
            download_distribution(self.root, "g0002")

    def test_checked_in_exact_carrier_is_definitively_withheld(self):
        data = read_distribution(ROOT, read_config(ROOT))
        item = data["generations"][0]
        self.assertEqual(item["carrier_sha256"],
                         "c1a86a55bece80df11cfc5e747cf016c83d4cf4ad5347dab9f7ce89233d0e58d")
        self.assertEqual(item["carrier_bytes"], 21919507382)
        self.assertEqual(item["publication_status"], "withheld")
        self.assertEqual(item["screening_status"], "blocked")
        self.assertFalse(item["clear_for_exact_publication"])
        self.assertEqual(item["parts"], [])
        self.assertIsNone(item["verification"])
