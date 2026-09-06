"""Separate publication, integrity, normative authority and runtime claims."""

import base64
import hashlib
import http.client
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from .common import (
    CatalogError, boolean, digest, enum, exact_keys, generation_id, hex_id, integer, load_json,
    path_fields, public_repo_url, public_text, require, safe_tree, timestamp, utc_now,
)


def read_authority(root):
    authority = load_json(Path(root) / "data" / "authority.json")
    exact_keys(authority, {"schema", "accepted", "content_source", "candidate", "runtime_activation"})
    require(authority["schema"] == "rapp-authority-context/2", "authority_schema")
    exact_keys(authority["accepted"], {"status", "revision", "commit", "repository_url"})
    exact_keys(authority["content_source"],
               {"status", "revision", "commit", "repository_url", "spec_path", "spec_sha256"})
    exact_keys(authority["candidate"], {"status", "commit", "pull_request_number", "pull_request_url"})
    accepted = authority["accepted"]
    content = authority["content_source"]
    candidate = authority["candidate"]
    enum(accepted["status"], {"accepted_canonical_checkpoint"})
    require(re.fullmatch(r"rev-[1-9][0-9]*", accepted["revision"]), "invalid_authority_revision")
    require(re.fullmatch(r"[0-9a-f]{8,40}", accepted["commit"]), "invalid_authority_commit")
    if accepted["repository_url"] is not None:
        require(public_repo_url(accepted["repository_url"]) == accepted["repository_url"],
                "invalid_authority_url")
    enum(content["status"], {"normative_content_source"})
    hex_id(content["commit"])
    require(content["revision"] == accepted["revision"], "authority_revision_mismatch")
    require(public_repo_url(content["repository_url"]) == content["repository_url"]
            == accepted["repository_url"], "content_source_repository_mismatch")
    public_text(content["spec_path"], 512)
    spec_path, _ = path_fields(base64.b64encode(content["spec_path"].encode("utf-8")).decode("ascii"))
    require(spec_path == content["spec_path"], "invalid_spec_path")
    hex_id(content["spec_sha256"], 64)
    enum(candidate["status"], {"reviewed_proposal_not_ratified"})
    hex_id(candidate["commit"])
    number = integer(candidate["pull_request_number"], 1)
    if candidate["pull_request_url"] is not None:
        require(accepted["repository_url"] is not None
                and candidate["pull_request_url"] == accepted["repository_url"] + f"/pull/{number}",
                "invalid_proposal_url")
    enum(authority["runtime_activation"], {"not_established"})
    safe_tree(authority)
    return authority


def descriptor_hash(carrier):
    return digest({key: carrier[key] for key in (
        "generation_id", "carrier_bytes", "carrier_sha256", "frame_hash", "parts",
    )})


def read_payload_policy(root):
    path = Path(root) / "data" / "payload-policy.json"
    require(path.is_file() and not path.is_symlink(), "invalid_payload_policy")
    require(path.stat().st_size <= 1024 * 1024, "payload_policy_too_large")
    value = load_json(path)
    exact_keys(value, {"schema", "encrypted_full_upload_allowed", "withheld_payloads"})
    require(value["schema"] == "rapp-public-payload-policy/1", "payload_policy_schema")
    require(value["encrypted_full_upload_allowed"] is False, "encrypted_full_upload_forbidden")
    require(isinstance(value["withheld_payloads"], list) and len(value["withheld_payloads"]) <= 1000,
            "invalid_withheld_payloads")
    generations, hashes = set(), set()
    for item in value["withheld_payloads"]:
        exact_keys(item, {"generation_id", "projection_of", "carrier_bytes", "reason"})
        generation_id(item["generation_id"])
        hex_id(item["projection_of"], 64)
        integer(item["carrier_bytes"], 1)
        enum(item["reason"], {"disclosure_and_rights_not_cleared"})
        require(item["generation_id"] not in generations and item["projection_of"] not in hashes,
                "duplicate_withheld_payload")
        generations.add(item["generation_id"])
        hashes.add(item["projection_of"])
    safe_tree(value)
    return value


def read_distribution(root, config, manifest=None):
    policy = read_payload_policy(root)
    path = Path(manifest) if manifest is not None else Path(root) / "data" / "distribution.json"
    require(path.is_file() and not path.is_symlink(), "invalid_distribution_file")
    require(path.stat().st_size <= 4 * 1024 * 1024, "distribution_manifest_too_large")
    value = load_json(path)
    exact_keys(value, {"schema", "generations"})
    require(value["schema"] == "rapp-public-distribution/3", "distribution_schema")
    require(isinstance(value["generations"], list), "invalid_distribution")
    seen = set()
    for item in value["generations"]:
        exact_keys(item, {"generation_id", "carrier_bytes", "carrier_sha256", "frame_hash",
                          "publication_status", "screening_status", "clear_for_exact_publication",
                          "parts", "verification"})
        generation_id(item["generation_id"])
        require(item["generation_id"] not in seen, "duplicate_distribution")
        seen.add(item["generation_id"])
        integer(item["carrier_bytes"], 1)
        hex_id(item["carrier_sha256"], 64)
        hex_id(item["frame_hash"], 64)
        enum(item["publication_status"], {"withheld", "not_published", "staged", "published"})
        enum(item["screening_status"], {"pending", "approved", "blocked"})
        boolean(item["clear_for_exact_publication"])
        require(item["clear_for_exact_publication"] == (item["screening_status"] == "approved"),
                "publication_clearance_mismatch")
        for denied in policy["withheld_payloads"]:
            if (denied["generation_id"] == item["generation_id"]
                    or denied["projection_of"] == item["carrier_sha256"]):
                require(denied["generation_id"] == item["generation_id"]
                        and denied["projection_of"] == item["carrier_sha256"]
                        and denied["carrier_bytes"] == item["carrier_bytes"],
                        "withheld_payload_identity_conflict")
                require(item["publication_status"] == "withheld"
                        and item["screening_status"] == "blocked"
                        and item["clear_for_exact_publication"] is False,
                        "withheld_payload_cannot_be_published")
        if item["publication_status"] == "withheld":
            require(item["screening_status"] == "blocked"
                    and item["clear_for_exact_publication"] is False, "invalid_withheld_state")
        require(isinstance(item["parts"], list) and len(item["parts"]) <= 1000,
                "invalid_distribution_parts")
        urls = set()
        size = 0
        for index, part in enumerate(item["parts"], 1):
            exact_keys(part, {"index", "offset", "bytes", "sha256", "url"})
            require(integer(part["index"], 1) == index, "unordered_distribution_parts")
            require(integer(part["offset"]) == size, "noncontiguous_distribution_offsets")
            require(integer(part["bytes"], 1) < 2 ** 31, "oversized_release_asset")
            hex_id(part["sha256"], 64)
            prefix = "https://github.com/" + config["observer_repository"] + "/releases/download/"
            require(isinstance(part["url"], str) and part["url"].startswith(prefix)
                    and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                                     part["url"][len(prefix):]), "invalid_release_asset_url")
            require(all(segment not in {".", ".."} for segment in part["url"][len(prefix):].split("/")),
                    "invalid_release_asset_url")
            require(part["url"] not in urls, "duplicate_release_asset")
            urls.add(part["url"])
            size += part["bytes"]
        if item["publication_status"] in {"withheld", "not_published"}:
            require(item["parts"] == [] and item["verification"] is None,
                    "unpublished_carrier_has_urls")
        else:
            require(item["screening_status"] == "approved"
                    and item["clear_for_exact_publication"] is True, "carrier_screening_incomplete")
            require(item["parts"] and size == item["carrier_bytes"], "carrier_size_mismatch")
        if item["publication_status"] == "published":
            proof = item["verification"]
            exact_keys(proof, {"schema", "method", "descriptor_sha256", "carrier_sha256",
                              "carrier_bytes", "verified_at"})
            require(proof["schema"] == "rapp-distribution-verification/1"
                    and proof["method"] == "downloaded_parts_and_assembled_sha256",
                    "unverified_distribution")
            require(hex_id(proof["descriptor_sha256"], 64) == descriptor_hash(item),
                    "distribution_proof_mismatch")
            require(proof["carrier_sha256"] == item["carrier_sha256"]
                    and proof["carrier_bytes"] == item["carrier_bytes"],
                    "distribution_proof_mismatch")
            timestamp(proof["verified_at"])
        elif item["verification"] is not None:
            require(False, "premature_distribution_proof")
    if manifest is None:
        identities = {(item["generation_id"], item["carrier_sha256"]) for item in value["generations"]}
        require(all((item["generation_id"], item["projection_of"]) in identities
                    for item in policy["withheld_payloads"]), "withheld_record_missing")
    safe_tree(value)
    return value


class AssetRedirects(urllib.request.HTTPRedirectHandler):
    """Release downloads may redirect only to GitHub's public asset hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlsplit
        parsed = urlsplit(newurl)
        require(parsed.scheme == "https" and parsed.hostname in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        } and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443), "unsafe_asset_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


BLOCK_BYTES = 1024 * 1024
DEFAULT_MAX_BYTES = 32 * 1024 ** 3
DEFAULT_MAX_SECONDS = 6 * 3600


class TransferBudget:
    def __init__(self, expected_bytes, max_bytes=DEFAULT_MAX_BYTES, max_seconds=DEFAULT_MAX_SECONDS):
        require(integer(expected_bytes, 1) <= integer(max_bytes, 1), "carrier_byte_budget_exceeded")
        require(0 < integer(max_seconds, 1) <= 24 * 3600, "invalid_transfer_time_budget")
        self.deadline = time.monotonic() + max_seconds

    def check(self):
        require(time.monotonic() < self.deadline, "transfer_time_budget_exceeded")

    def timeout(self):
        self.check()
        return max(0.1, min(60, self.deadline - time.monotonic()))


def select_distribution(root, config, generation, manifest=None, published=False):
    items = read_distribution(root, config, manifest)["generations"]
    item = next((entry for entry in items if entry["generation_id"] == generation), None)
    require(item is not None, "unknown_distribution")
    require(item["publication_status"] != "withheld", "carrier_withheld")
    if published:
        require(item["publication_status"] == "published", "carrier_not_publicly_published")
    return item


def asset_request(url, budget, start=0, opener=None):
    headers = {"User-Agent": "rapp-public-catalog/1", "Accept-Encoding": "identity"}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)
    opener = opener or urllib.request.build_opener(AssetRedirects())
    try:
        return opener.open(request, timeout=budget.timeout())
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise CatalogError("release_asset_download_failed") from error


def response_start(response, requested_start, part_bytes):
    status = response.status
    require(response.headers.get("Content-Encoding", "identity").lower() == "identity",
            "unexpected_content_encoding")
    if status == 206:
        expected = f"bytes {requested_start}-{part_bytes - 1}/{part_bytes}"
        require(response.headers.get("Content-Range") == expected, "invalid_content_range")
        start = requested_start
    else:
        require(status == 200, "release_asset_unavailable")
        start = 0
    length = response.headers.get("Content-Length")
    if length is not None:
        require(length.isdigit() and int(length) == part_bytes - start,
                "release_asset_content_length_mismatch")
    return start


def stream_blocks(stream, budget, expected_bytes):
    total = 0
    read = getattr(stream, "read1", stream.read)
    try:
        while True:
            budget.check()
            block = read(min(BLOCK_BYTES, expected_bytes - total + 1))
            if not block:
                break
            total += len(block)
            require(total <= expected_bytes, "release_asset_size_mismatch")
            yield block
        require(total == expected_bytes, "release_asset_truncated")
    except (OSError, http.client.HTTPException) as error:
        raise CatalogError("release_asset_download_interrupted") from error


def verify_distribution(root, config, generation, manifest=None, *,
                        max_bytes=DEFAULT_MAX_BYTES, max_seconds=DEFAULT_MAX_SECONDS):
    """Stream public chunks without saving or extracting the full carrier."""
    item = select_distribution(root, config, generation, manifest)
    require(item["publication_status"] in {"staged", "published"}, "carrier_not_publicly_published")
    require(item["screening_status"] == "approved", "carrier_screening_incomplete")
    budget = TransferBudget(item["carrier_bytes"], max_bytes, max_seconds)
    assembled = hashlib.sha256()
    total = 0
    opener = urllib.request.build_opener(AssetRedirects())
    try:
        for part in item["parts"]:
            h = hashlib.sha256()
            size = 0
            require(total == part["offset"], "noncontiguous_distribution_offsets")
            with asset_request(part["url"], budget, opener=opener) as response:
                require(response_start(response, 0, part["bytes"]) == 0, "invalid_content_range")
                for block in stream_blocks(response, budget, part["bytes"]):
                    size += len(block)
                    require(size <= part["bytes"], "release_asset_size_mismatch")
                    h.update(block)
                    assembled.update(block)
            require(size == part["bytes"] and h.hexdigest() == part["sha256"],
                    "release_asset_integrity_mismatch")
            total += size
    except (OSError, ValueError) as error:
        raise CatalogError("release_asset_download_failed") from error
    require(total == item["carrier_bytes"] and assembled.hexdigest() == item["carrier_sha256"],
            "carrier_integrity_mismatch")
    return {
        "schema": "rapp-distribution-verification/1",
        "method": "downloaded_parts_and_assembled_sha256",
        "descriptor_sha256": descriptor_hash(item), "carrier_sha256": assembled.hexdigest(),
        "carrier_bytes": total, "verified_at": utc_now(),
    }
