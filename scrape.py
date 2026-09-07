"""Fetch public data, write readable snapshots, and let Git keep the time series."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

from rapp_catalog.build import validate
from rapp_catalog.common import (
    CatalogError, atomic_bytes, boolean, canonical, decode_json, enum, exact_keys,
    hex_id, integer, load_json, require, safe_tree, timestamp,
)
from rapp_catalog.scrape import NoRedirect, scrape as scrape_repositories


WORLD_URL = "https://raw.githubusercontent.com/kody-w/dogg/main/orient.json"
FIELDS = {
    "bitcoin_block_height": ("btc_block_height", "height", "integer"),
    "bitcoin_usd": ("btc_usd", "spot", "number"),
    "bitcoin_fastest_fee_sat_vb": ("btc_fees", "fastest_sat_vb", "integer"),
    "bitcoin_mempool_transactions": ("btc_mempool", "tx_count", "integer"),
    "crypto_market_cap_usd": ("crypto_market", "total_mcap_usd", "number"),
    "bitcoin_dominance_percent": ("crypto_market", "btc_dominance_pct", "number"),
    "usd_eur": ("fx_usd", "EUR", "number"),
    "usd_gbp": ("fx_usd", "GBP", "number"),
    "usd_jpy": ("fx_usd", "JPY", "number"),
    "usd_cny": ("fx_usd", "CNY", "number"),
    "earthquakes_past_hour": ("earthquakes_past_hour", "count", "integer"),
    "largest_earthquake_magnitude": ("earthquakes_past_hour", "max_mag", "number"),
    "iss_latitude": ("iss", "lat", "number"),
    "iss_longitude": ("iss", "lon", "number"),
    "planetary_k_index": ("space_weather", "kp", "number"),
    "gb_grid_carbon_gco2_kwh": ("grid_carbon_gb", "gco2_kwh", "integer"),
    "hn_top_story_id": ("hn_top", "id", "integer"),
}


def number(value):
    require(type(value) in (str, int)
            and re.fullmatch(r"-?\d{1,20}(?:\.\d{1,12})?", str(value)),
            "invalid_public_number")
    parsed = Decimal(str(value))
    if parsed == 0:
        return "0"
    text = format(parsed, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def fetch_world():
    request = urllib.request.Request(WORLD_URL, headers={"Accept-Encoding": "identity"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            require(response.status == 200, "world_http_error")
            body = response.read(1024 * 1024 + 1)
            require(0 < len(body) <= 1024 * 1024, "world_response_size")
            length = response.headers.get("Content-Length")
            require(length is None or length.isdigit() and int(length) == len(body),
                    "world_response_truncated")
            return decode_json(body)
    except urllib.error.HTTPError as error:
        error.close()
        raise CatalogError("world_http_error") from error
    except (OSError, RecursionError) as error:
        raise CatalogError("world_fetch_failed") from error


def world_snapshot(raw):
    require(isinstance(raw, dict) and raw.get("schema") == "dogg/0-orient",
            "unsupported_world_source")
    world = raw.get("world")
    require(isinstance(world, dict) and isinstance(world.get("data"), dict),
            "world_observation_unavailable")
    statuses = raw.get("status")
    require(isinstance(statuses, dict), "world_status_missing")
    status = statuses.get("world")
    require(isinstance(status, dict), "world_status_missing")
    values = {}
    for name, (group, key, kind) in FIELDS.items():
        part = world["data"].get(group)
        require(part is None or isinstance(part, dict), "invalid_world_measurement")
        value = part.get(key) if part is not None else None
        values[name] = None if value is None else integer(value) if kind == "integer" else number(value)
    result = {
        "source": WORLD_URL,
        "source_recorded_at": world.get("utc"),
        "source_sequence": integer(world.get("seq")),
        "source_frame_hash": hex_id(world.get("frame_hash"), 64),
        "tick": integer(world.get("tick")),
        "upstream_refresh": enum(status.get("last_refresh"), {"ok", "failed"}),
        "retained_last_good": boolean(status.get("retained_last_good")),
        "values": values,
        "missing_values": sorted(name for name, value in values.items() if value is None),
    }
    check_world(result)
    return result


def check_world(value):
    exact_keys(value, {"source", "source_recorded_at", "source_sequence",
                       "source_frame_hash", "tick", "values", "missing_values",
                       "upstream_refresh", "retained_last_good"})
    require(value["source"] == WORLD_URL, "unexpected_world_source")
    timestamp(value["source_recorded_at"])
    integer(value["source_sequence"])
    integer(value["tick"])
    hex_id(value["source_frame_hash"], 64)
    enum(value["upstream_refresh"], {"ok", "failed"})
    boolean(value["retained_last_good"])
    exact_keys(value["values"], FIELDS)
    for name, item in value["values"].items():
        if item is not None:
            kind = FIELDS[name][2]
            require((integer(item) if kind == "integer" else number(item)) == item,
                    "noncanonical_world_measurement")
    missing = sorted(name for name, item in value["values"].items() if item is None)
    require(value["missing_values"] == missing and len(missing) < len(FIELDS),
            "invalid_world_coverage")
    safe_tree(value)


def repository_snapshot(bundle):
    require(bundle["events"], "recorded_public_observations_missing")
    return {
        "source": "https://api.github.com/users/" + bundle["config"]["owner"] + "/repos",
        "source_observed_at": bundle["events"][-1]["observed_at"],
        "repositories": sorted(bundle["state"].values(), key=lambda row: row["full_name"]),
    }


def check_world_progression(previous, current):
    check_world(previous)
    require(current["source_sequence"] >= previous["source_sequence"], "world_snapshot_rollback")
    if current["source_sequence"] == previous["source_sequence"]:
        # Source observations are immutable; upstream health can change independently.
        health = {"upstream_refresh", "retained_last_good"}
        old = {key: value for key, value in previous.items() if key not in health}
        new = {key: value for key, value in current.items() if key not in health}
        require(canonical(old) == canonical(new), "world_snapshot_conflict")
    else:
        require(current["source_frame_hash"] != previous["source_frame_hash"], "world_snapshot_conflict")


def write_snapshots(root, raw):
    root = Path(root)
    documents = {
        "repositories.json": repository_snapshot(validate(root)),
        "world.json": world_snapshot(raw),
    }
    directory = root / "snapshots"
    require(not directory.is_symlink(), "snapshot_symlink")
    changes = []
    for name, document in documents.items():
        safe_tree(document)
        path = directory / name
        require(not path.is_symlink(), "snapshot_symlink")
        require(not path.exists() or path.is_file() and path.stat().st_size < 8 * 1024 * 1024,
                "invalid_snapshot_file")
        body = (json.dumps(document, ensure_ascii=True, sort_keys=True, indent=2,
                           allow_nan=False) + "\n").encode("ascii")
        require(len(body) < 8 * 1024 * 1024, "snapshot_too_large")
        if name == "world.json" and path.exists():
            previous = load_json(path)
            check_world_progression(previous, document)
        if not path.exists() or path.read_bytes() != body:
            changes.append((path, body))
    for path, body in changes:
        atomic_bytes(path, body)
    return [path.relative_to(root).as_posix() for path, _ in changes]


def check_snapshots(root):
    root = Path(root)
    directory = root / "snapshots"
    require(directory.is_dir() and not directory.is_symlink(), "snapshots_missing")
    require({path.name for path in directory.iterdir()} == {"repositories.json", "world.json"},
            "unexpected_snapshot_file")
    for path in directory.iterdir():
        require(path.is_file() and not path.is_symlink() and path.stat().st_size < 8 * 1024 * 1024,
                "invalid_snapshot_file")
    require(canonical(load_json(directory / "repositories.json"))
            == canonical(repository_snapshot(validate(root))),
            "repository_snapshot_stale")
    check_world(load_json(directory / "world.json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--recorded", action="store_true",
                      help="Reuse committed repository observations; still fetch public DOGG data.")
    mode.add_argument("--check", action="store_true", help="Check committed snapshots without network reads.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        if args.check:
            check_snapshots(root)
            result = {"snapshots_valid": True}
        else:
            raw = fetch_world()
            world_snapshot(raw)
            if not args.recorded:
                scrape_repositories(root)
            result = {"changed": write_snapshots(root, raw)}
            check_snapshots(root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CatalogError, OSError) as error:
        code = error.code if isinstance(error, CatalogError) else "snapshot_io_error"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
