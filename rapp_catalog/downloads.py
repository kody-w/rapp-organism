"""Bounded public carrier recovery. Cache only opaque bytes; never extract or execute."""

import contextlib
import hashlib
import os
from pathlib import Path
import re
import shutil

from .common import (
    CatalogError, atomic_json, canonical, exact_keys, load_json, read_config, require,
)
from .distribution import (
    DEFAULT_MAX_BYTES, DEFAULT_MAX_SECONDS, TransferBudget, asset_request,
    descriptor_hash, response_start, select_distribution, stream_blocks,
)


STATE_FILE = ".download-state.json"
LOCK_FILE = ".download.lock"


def part_name(part, partial=False):
    return f"part-{part['index']:04d}." + ("partial" if partial else "bin")


def carrier_name(item):
    return "carrier-" + item["carrier_sha256"] + ".egg.zip"


def regular(path):
    require(not path.is_symlink(), "download_symlink_refused")
    if path.exists():
        require(path.is_file(), "download_nonregular_file")
        return True
    return False


def cache_directory(root, directory, generation):
    root = Path(root).resolve()
    target = Path(directory) if directory is not None else Path("downloads") / generation
    if not target.is_absolute():
        target = root / target
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise CatalogError("download_directory_outside_repository") from error
    require(len(relative.parts) >= 2 and relative.parts[0] == "downloads",
            "download_directory_must_be_under_downloads")
    require(all(part not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9_.-]+", part)
                for part in relative.parts), "unsafe_download_directory")
    current = root
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), "download_symlink_refused")
        if current.exists():
            require(current.is_dir(), "invalid_download_directory")
        else:
            current.mkdir(mode=0o700)
    return current


@contextlib.contextmanager
def locked_cache(root, item, directory):
    cache = cache_directory(root, directory, item["generation_id"])
    lock = cache / LOCK_FILE
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise CatalogError("carrier_cache_in_use") from error
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical({"pid": os.getpid(), "descriptor_sha256": descriptor_hash(item)}))
            stream.flush()
            os.fsync(stream.fileno())
        expected = {
            "schema": "rapp-carrier-cache/1", "descriptor_sha256": descriptor_hash(item),
            "generation_id": item["generation_id"], "carrier_sha256": item["carrier_sha256"],
        }
        state = cache / STATE_FILE
        if regular(state):
            recorded = load_json(state)
            exact_keys(recorded, expected)
            require(recorded == expected, "carrier_cache_manifest_mismatch")
        else:
            require({path.name for path in cache.iterdir()} == {LOCK_FILE},
                    "download_directory_not_empty")
            atomic_json(state, expected)
        allowed = {STATE_FILE, LOCK_FILE, carrier_name(item), "." + carrier_name(item) + ".assembling"}
        for part in item["parts"]:
            allowed.update({part_name(part), part_name(part, partial=True)})
        for path in cache.iterdir():
            require(path.name in allowed, "unrecognized_download_file")
            regular(path)
        yield cache
    finally:
        lock.unlink(missing_ok=True)


def file_matches(path, expected_bytes, expected_sha256, budget):
    if not regular(path) or path.stat().st_size != expected_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in stream_blocks(stream, budget, expected_bytes):
            digest.update(block)
    return digest.hexdigest() == expected_sha256


def require_space(directory, needed):
    require(shutil.disk_usage(directory).free >= needed, "insufficient_download_space")


def download_part(cache, part, budget, repair=False, opener=None):
    finished = cache / part_name(part)
    partial = cache / part_name(part, partial=True)
    if regular(finished):
        if file_matches(finished, part["bytes"], part["sha256"], budget):
            return {"reused": True, "resumed": False, "transferred_bytes": 0}
        require(repair, "cached_part_integrity_mismatch")
        finished.unlink()
    if regular(partial):
        size = partial.stat().st_size
        if size == part["bytes"] and file_matches(partial, part["bytes"], part["sha256"], budget):
            require(not finished.exists(), "part_destination_conflict")
            os.replace(partial, finished)
            return {"reused": True, "resumed": False, "transferred_bytes": 0}
        if repair:
            partial.unlink()
        else:
            require(size < part["bytes"], "cached_partial_integrity_mismatch")
    start = partial.stat().st_size if regular(partial) else 0
    budget.check()
    transferred = 0
    with asset_request(part["url"], budget, start=start, opener=opener) as response:
        accepted_start = response_start(response, start, part["bytes"])
        if accepted_start:
            require(regular(partial) and partial.stat().st_size == accepted_start,
                    "partial_changed_during_download")
        with partial.open("ab" if accepted_start else "wb") as output:
            for block in stream_blocks(response, budget, part["bytes"] - accepted_start):
                output.write(block)
                transferred += len(block)
            output.flush()
            os.fsync(output.fileno())
    require(file_matches(partial, part["bytes"], part["sha256"], budget),
            "downloaded_part_integrity_mismatch")
    require(not finished.exists() and not finished.is_symlink(), "part_destination_conflict")
    os.replace(partial, finished)
    return {"reused": False, "resumed": bool(accepted_start), "transferred_bytes": transferred}


def download_distribution(root, generation="g0002", directory=None, manifest=None, *,
                          repair=False, max_bytes=DEFAULT_MAX_BYTES,
                          max_seconds=DEFAULT_MAX_SECONDS, opener=None):
    item = select_distribution(root, read_config(root), generation, manifest, published=True)
    budget = TransferBudget(item["carrier_bytes"], max_bytes, max_seconds)
    result = {
        "status": "parts_verified", "generation_id": generation,
        "descriptor_sha256": descriptor_hash(item), "carrier_bytes": item["carrier_bytes"],
        "carrier_sha256": item["carrier_sha256"], "parts": len(item["parts"]),
        "reused_parts": 0, "downloaded_parts": 0, "resumed_parts": 0, "transferred_bytes": 0,
        "publication_status": item["publication_status"], "screening_status": item["screening_status"],
        "clear_for_exact_publication": item["clear_for_exact_publication"],
        "archive_assembled": False, "contents_extracted": False, "runtime_activated": False,
    }
    with locked_cache(root, item, directory) as cache:
        remaining = 0
        for part in item["parts"]:
            finished = cache / part_name(part)
            partial = cache / part_name(part, partial=True)
            existing = finished if regular(finished) else partial
            present = existing.stat().st_size if regular(existing) else 0
            remaining += max(0, part["bytes"] - present)
        require_space(cache, remaining)
        for part in item["parts"]:
            budget.check()
            outcome = download_part(cache, part, budget, repair, opener)
            result["reused_parts"] += int(outcome["reused"])
            result["downloaded_parts"] += int(not outcome["reused"])
            result["resumed_parts"] += int(outcome["resumed"])
            result["transferred_bytes"] += outcome["transferred_bytes"]
        result["directory"] = cache.relative_to(Path(root).resolve()).as_posix()
    return result


def assemble_distribution(root, generation="g0002", directory=None, manifest=None, *,
                          repair=False, max_bytes=DEFAULT_MAX_BYTES,
                          max_seconds=DEFAULT_MAX_SECONDS):
    item = select_distribution(root, read_config(root), generation, manifest, published=True)
    budget = TransferBudget(item["carrier_bytes"], max_bytes, max_seconds)
    with locked_cache(root, item, directory) as cache:
        final = cache / carrier_name(item)
        result = {
            "status": "carrier_integrity_verified", "generation_id": generation,
            "descriptor_sha256": descriptor_hash(item), "carrier_bytes": item["carrier_bytes"],
            "carrier_sha256": item["carrier_sha256"],
            "file": final.relative_to(Path(root).resolve()).as_posix(),
            "publication_status": item["publication_status"], "screening_status": item["screening_status"],
            "clear_for_exact_publication": item["clear_for_exact_publication"],
            "contents_extracted": False, "runtime_activated": False, "reused": False,
        }
        if regular(final):
            if file_matches(final, item["carrier_bytes"], item["carrier_sha256"], budget):
                return {**result, "reused": True}
            require(repair, "existing_carrier_integrity_mismatch")
            final.unlink()
        stage = cache / ("." + carrier_name(item) + ".assembling")
        if regular(stage):
            stage.unlink()
        require_space(cache, item["carrier_bytes"])
        whole = hashlib.sha256()
        written = 0
        try:
            with stage.open("xb") as output:
                for part in item["parts"]:
                    budget.check()
                    require(written == part["offset"], "noncontiguous_distribution_offsets")
                    path = cache / part_name(part)
                    require(regular(path), "carrier_part_missing")
                    require(path.stat().st_size == part["bytes"], "cached_part_integrity_mismatch")
                    digest = hashlib.sha256()
                    with path.open("rb") as source:
                        for block in stream_blocks(source, budget, part["bytes"]):
                            digest.update(block)
                            whole.update(block)
                            output.write(block)
                            written += len(block)
                    require(digest.hexdigest() == part["sha256"], "cached_part_integrity_mismatch")
                output.flush()
                os.fsync(output.fileno())
            require(written == item["carrier_bytes"] and whole.hexdigest() == item["carrier_sha256"],
                    "assembled_carrier_integrity_mismatch")
            require(not final.exists() and not final.is_symlink(), "carrier_destination_conflict")
            os.replace(stage, final)
            return result
        finally:
            stage.unlink(missing_ok=True)


def verify_carrier(root, file, generation="g0002", manifest=None, *,
                   max_bytes=DEFAULT_MAX_BYTES, max_seconds=DEFAULT_MAX_SECONDS):
    item = select_distribution(root, read_config(root), generation, manifest)
    budget = TransferBudget(item["carrier_bytes"], max_bytes, max_seconds)
    path = Path(file)
    if not path.is_absolute():
        path = Path(root) / path
    require(regular(path), "carrier_file_missing")
    require(file_matches(path, item["carrier_bytes"], item["carrier_sha256"], budget),
            "carrier_integrity_mismatch")
    return {
        "status": "carrier_integrity_verified", "generation_id": generation,
        "carrier_bytes": item["carrier_bytes"], "carrier_sha256": item["carrier_sha256"],
        "publication_status": item["publication_status"], "screening_status": item["screening_status"],
        "clear_for_exact_publication": item["clear_for_exact_publication"],
        "contents_extracted": False, "runtime_activated": False,
    }
