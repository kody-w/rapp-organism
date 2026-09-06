import copy
import io
from unittest import mock

import scrape as pipeline
from rapp_catalog.common import CatalogError, load_json
from rapp_catalog.scrape import scrape
from tests.helpers import CatalogCase, FixtureClient, LATER, NOW


def world():
    return {
        "schema": "dogg/0-orient", "generated_utc": NOW,
        "status": {"world": {"last_refresh": "ok", "retained_last_good": False}},
        "world": {
            "utc": NOW, "seq": 8, "tick": 9, "frame_hash": "a" * 64,
            "data": {
                "btc_usd": {"spot": "12.5000"},
                "earthquakes_past_hour": {"count": 3, "max_mag": "2.1"},
                "iss": {"lat": "-44.2", "lon": "100.0"},
                "hn_top": {"id": 123, "title": "Do not copy arbitrary source text"},
                "unselected": {"note": "Do not publish this unselected content"},
            },
        },
    }


class SnapshotTests(CatalogCase):
    def setUp(self):
        super().setUp()
        scrape(self.root, client=FixtureClient(), now=NOW)

    def test_public_raw_values_become_readable_allowlisted_json(self):
        changed = pipeline.write_snapshots(self.root, world())
        self.assertEqual(changed, ["snapshots/repositories.json", "snapshots/world.json"])
        pipeline.check_snapshots(self.root)
        data = load_json(self.root / "snapshots/world.json")
        self.assertEqual(data["values"]["bitcoin_usd"], "12.5")
        self.assertEqual(data["values"]["iss_longitude"], "100")
        self.assertEqual(data["values"]["earthquakes_past_hour"], 3)
        self.assertIn("gb_grid_carbon_gco2_kwh", data["missing_values"])
        text = (self.root / "snapshots/world.json").read_text()
        self.assertNotIn("Do not", text)
        self.assertNotIn("unselected", text)
        self.assertNotIn("generated_utc", text)

    def test_poll_clock_and_unrelated_raw_changes_do_not_rewrite_snapshots(self):
        raw = world()
        pipeline.write_snapshots(self.root, raw)
        before = {p.name: p.read_bytes() for p in (self.root / "snapshots").iterdir()}
        scrape(self.root, client=FixtureClient(cache=self.cache()), now=LATER)
        raw["generated_utc"] = LATER
        raw["dimensions"] = [{"unrelated": "metadata"}]
        self.assertEqual(pipeline.write_snapshots(self.root, raw), [])
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "snapshots").iterdir()})

    def test_real_source_movement_changes_only_world_snapshot(self):
        raw = world()
        pipeline.write_snapshots(self.root, raw)
        moved = copy.deepcopy(raw)
        moved["world"].update(utc=LATER, seq=9, frame_hash="b" * 64)
        moved["world"]["data"]["iss"]["lat"] = "-40.8"
        self.assertEqual(pipeline.write_snapshots(self.root, moved), ["snapshots/world.json"])
        self.assertEqual(load_json(self.root / "snapshots/world.json")["values"]["iss_latitude"], "-40.8")
        pipeline.check_snapshots(self.root)

    def test_upstream_retention_is_recorded_not_relabelled_as_fresh(self):
        raw = world()
        pipeline.write_snapshots(self.root, raw)
        raw["status"]["world"] = {"last_refresh": "failed", "retained_last_good": True}
        self.assertEqual(pipeline.write_snapshots(self.root, raw), ["snapshots/world.json"])
        data = load_json(self.root / "snapshots/world.json")
        self.assertEqual(data["source_recorded_at"], NOW)
        self.assertTrue(data["retained_last_good"])
        self.assertEqual(data["upstream_refresh"], "failed")

    def test_malformed_data_preserves_both_last_good_files(self):
        pipeline.write_snapshots(self.root, world())
        before = {p.name: p.read_bytes() for p in (self.root / "snapshots").iterdir()}
        for invalid in (True, "not-a-number", "NaN", {"unexpected": 1}):
            raw = world()
            raw["world"]["data"]["btc_usd"]["spot"] = invalid
            with self.assertRaises(CatalogError):
                pipeline.write_snapshots(self.root, raw)
            self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / "snapshots").iterdir()})

    def test_http_errors_and_truncation_are_not_successful_snapshots(self):
        for status, headers in ((206, {}), (200, {"Content-Length": "1000"})):
            response = io.BytesIO(b"{}")
            response.status, response.headers = status, headers
            opener = mock.Mock()
            opener.open.return_value = response
            with mock.patch("scrape.urllib.request.build_opener", return_value=opener):
                with self.assertRaises(CatalogError):
                    pipeline.fetch_world()
            self.assertTrue(response.closed)
