import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest import mock
import urllib.request

from rapp_catalog.build import build, query
from rapp_catalog.common import CatalogError, canonical, file_hash, load_json
from rapp_catalog.distribution import TransferBudget, descriptor_hash, read_distribution
from rapp_catalog.downloads import (
    assemble_distribution, carrier_name, download_distribution, locked_cache,
    part_name, verify_carrier,
)
from tests.helpers import CatalogCase


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}


class Assets:
    def __init__(self, bodies, override=None, ignore_range=False):
        self.bodies = bodies
        self.override = override
        self.ignore_range = ignore_range
        self.calls = []

    def open(self, request, timeout):
        index = int(request.full_url.rsplit("-", 1)[-1])
        header = request.get_header("Range")
        start = int(header.removeprefix("bytes=").removesuffix("-")) if header else 0
        self.calls.append((index, start))
        if self.override:
            result = self.override(index, start)
            if result is not None:
                return result
        body = self.bodies[index - 1]
        if start and not self.ignore_range:
            return Response(body[start:], 206, {
                "Content-Length": str(len(body) - start),
                "Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}",
            })
        return Response(body)


class DownloadTests(CatalogCase):
    def setUp(self):
        super().setUp()
        self.bodies = [b"0123456789", b"abcdef", b"XYZ!"]
        self.whole = b"".join(self.bodies)
        value = load_json(self.root / "data" / "distribution.json")
        self.item = value["generations"][0]
        self.item.update(
            carrier_bytes=len(self.whole), carrier_sha256=hashlib.sha256(self.whole).hexdigest(),
            publication_status="published", screening_status="approved",
            clear_for_exact_publication=True,
        )
        self.item["parts"] = [
            {"index": i, "offset": sum(map(len, self.bodies[:i - 1])), "bytes": len(body),
             "sha256": hashlib.sha256(body).hexdigest(),
             "url": f"https://github.com/example/catalog/releases/download/g0002/part-{i:03d}"}
            for i, body in enumerate(self.bodies, 1)
        ]
        self.value = value
        self.save_index()
        self.directory = self.root / "downloads" / "g0002"

    def save_index(self):
        if self.item["publication_status"] == "published":
            self.item["verification"] = {
                "schema": "rapp-distribution-verification/1",
                "method": "downloaded_parts_and_assembled_sha256",
                "descriptor_sha256": descriptor_hash(self.item),
                "carrier_sha256": self.item["carrier_sha256"],
                "carrier_bytes": self.item["carrier_bytes"],
                "verified_at": "2026-01-02T00:00:00Z",
            }
        (self.root / "data" / "distribution.json").write_bytes(canonical(self.value))

    def download(self, opener=None, **kwargs):
        return download_distribution(self.root, opener=opener or Assets(self.bodies), **kwargs)

    def interrupted_prefix(self):
        def truncated(index, start):
            if index == 1:
                return Response(self.bodies[0][:4], headers={"Content-Length": str(len(self.bodies[0]))})
            return None
        with self.assertRaisesRegex(CatalogError, "release_asset_truncated"):
            self.download(Assets(self.bodies, override=truncated))
        partial = self.directory / part_name(self.item["parts"][0], partial=True)
        self.assertEqual(partial.read_bytes(), self.bodies[0][:4])
        self.assertFalse((self.directory / part_name(self.item["parts"][0])).exists())
        return partial

    def test_download_assemble_verify_exact_bytes_and_idempotent_reuse(self):
        assets = Assets(self.bodies)
        result = self.download(assets)
        self.assertEqual(result["status"], "parts_verified")
        self.assertEqual(result["transferred_bytes"], len(self.whole))
        self.assertEqual(result["downloaded_parts"], 3)
        self.assertFalse(result["archive_assembled"])
        for part, body in zip(self.item["parts"], self.bodies, strict=True):
            self.assertEqual((self.directory / part_name(part)).read_bytes(), body)
        assembly = assemble_distribution(self.root)
        archive = self.root / assembly["file"]
        self.assertEqual(archive.read_bytes(), self.whole)
        self.assertFalse(assembly["contents_extracted"])
        self.assertFalse(assembly["runtime_activated"])
        self.assertEqual(verify_carrier(self.root, archive)["status"], "carrier_integrity_verified")
        before = archive.stat().st_mtime_ns
        self.assertTrue(assemble_distribution(self.root)["reused"])
        self.assertEqual(archive.stat().st_mtime_ns, before)
        again = Assets(self.bodies)
        self.assertEqual(self.download(again)["reused_parts"], 3)
        self.assertEqual(again.calls, [])

    def test_resume_uses_chunk_local_range_and_keeps_verified_prefix(self):
        self.interrupted_prefix()
        assets = Assets(self.bodies)
        result = self.download(assets)
        self.assertEqual(assets.calls[0], (1, 4))
        self.assertEqual(result["resumed_parts"], 1)
        self.assertEqual(result["transferred_bytes"], len(self.whole) - 4)
        archive = self.root / assemble_distribution(self.root)["file"]
        self.assertEqual(archive.read_bytes(), self.whole)

    def test_server_ignoring_range_restarts_instead_of_appending_full_body(self):
        self.interrupted_prefix()
        result = self.download(Assets(self.bodies, ignore_range=True))
        self.assertEqual(result["resumed_parts"], 0)
        self.assertEqual(result["transferred_bytes"], len(self.whole))
        self.assertEqual((self.directory / part_name(self.item["parts"][0])).read_bytes(), self.bodies[0])

    def test_bad_content_range_or_length_preserves_partial_without_publication(self):
        partial = self.interrupted_prefix()
        prefix = partial.read_bytes()
        for headers in (
            {"Content-Range": "bytes 0-5/10", "Content-Length": "6"},
            {"Content-Range": "bytes 4-9/11", "Content-Length": "6"},
            {"Content-Range": "bytes 4-8/10", "Content-Length": "6"},
            {"Content-Range": "bytes 4-9/10", "Content-Length": "5"},
        ):
            with self.subTest(headers=headers):
                assets = Assets(self.bodies, override=lambda index, start:
                                Response(self.bodies[0][4:], 206, headers) if index == 1 else None)
                with self.assertRaises(CatalogError):
                    self.download(assets)
                self.assertEqual(partial.read_bytes(), prefix)
                self.assertFalse((self.directory / part_name(self.item["parts"][0])).exists())

    def test_corrupt_download_is_never_promoted_and_repair_retries_safely(self):
        bad = Assets(self.bodies, override=lambda index, start:
                     Response(b"badbadbad!") if index == 1 else None)
        with self.assertRaisesRegex(CatalogError, "downloaded_part_integrity_mismatch"):
            self.download(bad)
        self.assertFalse((self.directory / part_name(self.item["parts"][0])).exists())
        with self.assertRaisesRegex(CatalogError, "cached_partial_integrity_mismatch"):
            self.download()
        self.assertEqual(self.download(repair=True)["downloaded_parts"], 3)
        self.assertEqual((self.root / assemble_distribution(self.root)["file"]).read_bytes(), self.whole)

    def test_corrupt_completed_part_requires_explicit_repair(self):
        self.download()
        corrupt = self.directory / part_name(self.item["parts"][1])
        corrupt.write_bytes(b"badbad")
        with self.assertRaisesRegex(CatalogError, "cached_part_integrity_mismatch"):
            self.download()
        self.assertEqual(corrupt.read_bytes(), b"badbad")
        assets = Assets(self.bodies)
        repaired = self.download(assets, repair=True)
        self.assertEqual(assets.calls, [(2, 0)])
        self.assertEqual(repaired["reused_parts"], 2)
        self.assertEqual(corrupt.read_bytes(), self.bodies[1])

    def test_assembly_rehashes_cached_parts_and_never_publishes_corruption(self):
        self.download()
        corrupt = self.directory / part_name(self.item["parts"][2])
        corrupt.write_bytes(b"BAD!")
        with self.assertRaisesRegex(CatalogError, "cached_part_integrity_mismatch"):
            assemble_distribution(self.root)
        self.assertFalse((self.directory / carrier_name(self.item)).exists())
        self.assertFalse(any(p.name.endswith(".assembling") for p in self.directory.iterdir()))

    def test_whole_hash_mismatch_blocks_archive_even_when_all_parts_match(self):
        self.item["carrier_sha256"] = "0" * 64
        self.save_index()
        self.download()
        with self.assertRaisesRegex(CatalogError, "assembled_carrier_integrity_mismatch"):
            assemble_distribution(self.root)
        self.assertFalse((self.directory / carrier_name(self.item)).exists())

    def test_corrupt_existing_archive_is_not_overwritten_without_repair(self):
        self.download()
        path = self.root / assemble_distribution(self.root)["file"]
        path.write_bytes(b"x" * len(self.whole))
        with self.assertRaisesRegex(CatalogError, "existing_carrier_integrity_mismatch"):
            assemble_distribution(self.root)
        self.assertEqual(path.read_bytes(), b"x" * len(self.whole))
        self.assertFalse(assemble_distribution(self.root, repair=True)["reused"])
        self.assertEqual(path.read_bytes(), self.whole)

    def test_manifest_changes_cannot_mix_with_an_existing_cache(self):
        self.download()
        before = file_hash(self.directory / part_name(self.item["parts"][0]))
        self.item["parts"][0]["url"] = self.item["parts"][0]["url"].replace("/g0002/", "/g0002-new/")
        self.save_index()
        assets = Assets(self.bodies)
        with self.assertRaisesRegex(CatalogError, "carrier_cache_manifest_mismatch"):
            self.download(assets)
        self.assertEqual(assets.calls, [])
        self.assertEqual(file_hash(self.directory / part_name(self.item["parts"][0])), before)

    def test_offsets_order_and_totals_fail_before_any_download(self):
        original = canonical(self.value)
        for field, value in (("offset", 9), ("offset", 11), ("index", 3), ("index", True)):
            with self.subTest(field=field, value=value):
                self.value = json.loads(original)
                self.item = self.value["generations"][0]
                self.item["parts"][1][field] = value
                self.save_index()
                assets = Assets(self.bodies)
                with self.assertRaises(CatalogError):
                    self.download(assets)
                self.assertEqual(assets.calls, [])
                self.assertFalse(self.directory.exists())
        self.value = json.loads(original)
        self.item = self.value["generations"][0]
        self.item["carrier_bytes"] += 1
        self.save_index()
        with self.assertRaisesRegex(CatalogError, "carrier_size_mismatch"):
            self.download()

    def test_pending_and_staged_carriers_are_not_public_downloads(self):
        for status in ("staged", "not_published"):
            self.item["publication_status"] = status
            self.item["verification"] = None
            if status == "not_published":
                self.item["parts"] = []
                self.item["screening_status"] = "pending"
                self.item["clear_for_exact_publication"] = False
            self.save_index()
            with self.assertRaisesRegex(CatalogError, "carrier_not_publicly_published"):
                self.download()
            with self.assertRaisesRegex(CatalogError, "carrier_not_publicly_published"):
                assemble_distribution(self.root)
            self.assertFalse((self.root / "downloads").exists())

    def test_local_hash_verification_does_not_claim_pending_carrier_is_public(self):
        path = self.root / "known-local.egg.zip"
        path.write_bytes(self.whole)
        self.item.update(publication_status="not_published", screening_status="pending",
                         clear_for_exact_publication=False, parts=[], verification=None)
        self.save_index()
        result = verify_carrier(self.root, path)
        self.assertEqual(result["publication_status"], "not_published")
        self.assertEqual(result["screening_status"], "pending")
        self.assertFalse(result["runtime_activated"])
        path.write_bytes(b"wrong")
        with self.assertRaisesRegex(CatalogError, "carrier_integrity_mismatch"):
            verify_carrier(self.root, path)

    def test_byte_time_and_space_budgets_are_fail_closed(self):
        with self.assertRaisesRegex(CatalogError, "carrier_byte_budget_exceeded"):
            self.download(max_bytes=len(self.whole) - 1)
        self.assertFalse((self.root / "downloads").exists())
        assets = Assets(self.bodies)
        with mock.patch("rapp_catalog.downloads.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(CatalogError, "insufficient_download_space"):
                self.download(assets)
        self.assertEqual(assets.calls, [])
        with mock.patch("rapp_catalog.distribution.time.monotonic", return_value=0):
            budget = TransferBudget(len(self.whole), max_seconds=1)
        with mock.patch("rapp_catalog.distribution.time.monotonic", return_value=2):
            with self.assertRaisesRegex(CatalogError, "transfer_time_budget_exceeded"):
                budget.check()

    def test_paths_symlinks_and_nonempty_directories_are_not_overwritten(self):
        for directory in (self.root / "data", self.root / "downloads" / ".." / "data", self.work / "elsewhere"):
            with self.subTest(directory=directory.name):
                with self.assertRaises(CatalogError):
                    self.download(directory=directory)
        self.directory.mkdir(parents=True)
        user_file = self.directory / "user-file.txt"
        user_file.write_bytes(b"keep")
        with self.assertRaisesRegex(CatalogError, "download_directory_not_empty"):
            self.download()
        self.assertEqual(user_file.read_bytes(), b"keep")
        user_file.unlink()
        self.directory.rmdir()
        self.directory.symlink_to(self.input_dir, target_is_directory=True)
        with self.assertRaisesRegex(CatalogError, "download_symlink_refused"):
            self.download()

    def test_symlinked_part_is_never_followed_even_with_repair(self):
        self.download()
        path = self.directory / part_name(self.item["parts"][0])
        path.unlink()
        path.symlink_to(self.paths["scope"])
        before = file_hash(self.paths["scope"])
        with self.assertRaisesRegex(CatalogError, "download_symlink_refused"):
            self.download(repair=True)
        self.assertEqual(file_hash(self.paths["scope"]), before)

    def test_lock_prevents_concurrent_cache_writers(self):
        with locked_cache(self.root, self.item, None):
            with self.assertRaisesRegex(CatalogError, "carrier_cache_in_use"):
                self.download()
        self.assertFalse((self.directory / ".download.lock").exists())

    def test_unknown_length_extra_bytes_and_content_encoding_are_rejected(self):
        for response in (
            lambda: Response(self.bodies[0] + b"x", headers={}),
            lambda: Response(self.bodies[0], headers={"Content-Encoding": "gzip"}),
        ):
            with self.subTest(response=response):
                assets = Assets(self.bodies, override=lambda index, start:
                                response() if index == 1 else None)
                with self.assertRaises(CatalogError):
                    self.download(assets, repair=True)
                self.assertFalse((self.directory / part_name(self.item["parts"][0])).exists())

    def test_public_external_index_and_sql_offsets_use_the_same_contract(self):
        index = self.root / "release-index.json"
        index.write_bytes(canonical(self.value))
        self.download(manifest=index)
        build(self.root, self.root / "site")
        rows = query(self.root / "site" / "catalog.sqlite",
                     "SELECT part_index,byte_offset,bytes FROM release_parts ORDER BY part_index")["rows"]
        self.assertEqual(rows, [(1, 0, 10), (2, 10, 6), (3, 16, 4)])

    def test_generic_split_plan_is_exact_and_strictly_under_release_limit(self):
        one_gib = 1024 ** 3
        total = 2 * one_gib + 123
        lengths = [one_gib] * (total // one_gib) + [total % one_gib]
        self.assertEqual(len(lengths), 3)
        self.assertEqual(lengths[-1], 123)
        self.assertEqual(sum(lengths[:-1]), 2 * one_gib)
        self.assertEqual(sum(lengths), total)
        self.assertTrue(all(0 < length < 2 ** 31 for length in lengths))

    def test_documented_cli_round_trip_and_interrupt_exit_codes(self):
        from rapp_catalog.__main__ import main
        with contextlib.chdir(self.root):
            output = io.StringIO()
            with mock.patch("rapp_catalog.distribution.urllib.request.build_opener",
                            return_value=Assets(self.bodies)), contextlib.redirect_stdout(output):
                self.assertEqual(main(["download-distribution"]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "parts_verified")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["assemble-distribution"]), 0)
            archive = json.loads(output.getvalue())["file"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["verify-carrier", "--file", archive]), 0)
            self.assertEqual(json.loads(output.getvalue())["carrier_sha256"],
                             hashlib.sha256(self.whole).hexdigest())
            errors = io.StringIO()
            with mock.patch("rapp_catalog.downloads.download_distribution", side_effect=KeyboardInterrupt), \
                    contextlib.redirect_stderr(errors):
                self.assertEqual(main(["download-distribution"]), 130)
            self.assertEqual(json.loads(errors.getvalue()), {"error": "operation_interrupted"})

    def test_actual_http_truncation_resume_and_reassembly(self):
        bodies = self.bodies
        requests = []
        interrupted = False

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                nonlocal interrupted
                index = int(self.path.strip("/"))
                body = bodies[index - 1]
                requested = self.headers.get("Range")
                start = int(requested.removeprefix("bytes=").removesuffix("-")) if requested else 0
                requests.append((index, start))
                self.send_response(206 if requested else 200)
                self.send_header("Content-Length", str(len(body) - start))
                if requested:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.end_headers()
                if index == 1 and not interrupted:
                    interrupted = True
                    self.wfile.write(body[:4])
                    self.close_connection = True
                else:
                    self.wfile.write(body[start:])

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}/"

        class LocalTransport:
            def open(self, request, timeout):
                index = int(request.full_url.rsplit("-", 1)[-1])
                mapped = urllib.request.Request(base + str(index), headers=dict(request.headers))
                return urllib.request.urlopen(mapped, timeout=timeout)

        try:
            with self.assertRaises(CatalogError):
                self.download(LocalTransport())
            result = self.download(LocalTransport())
            self.assertEqual(result["resumed_parts"], 1)
            self.assertIn((1, 4), requests)
            archive = self.root / assemble_distribution(self.root)["file"]
            self.assertEqual(archive.read_bytes(), self.whole)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
