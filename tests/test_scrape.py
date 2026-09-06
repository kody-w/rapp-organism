import copy
import hashlib
import http.client
import io
import json
from pathlib import Path
from unittest import mock

from rapp_catalog.build import build, query, validate
from rapp_catalog.common import CatalogError, canonical, load_json
from rapp_catalog.observations import initial_state, replay_events, state_digest
from rapp_catalog.scrape import GitHubClient, poll, scrape
from tests.helpers import CatalogCase, FixtureClient, HEAD, LATER, NEW_HEAD, NOW, raw_repo


class ScrapeTests(CatalogCase):
    def first(self):
        return scrape(self.root, client=FixtureClient(), now=NOW)

    def test_identical_poll_is_true_noop_but_has_fresh_receipt(self):
        first = self.first()
        before = self.public_hashes()
        second_client = FixtureClient(cache=self.cache())
        second = scrape(self.root, client=second_client, now=LATER)
        self.assertEqual(second["semantic_changes"], 0)
        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(before, self.public_hashes())
        self.assertEqual(second["last_success_at"], LATER)
        self.assertEqual(second_client.requests, 4)
        self.assertFalse(any("/git/ref/" in path for path, _ in second_client.calls))
        self.assertTrue(all("If-None-Match" in headers for _, headers in second_client.calls))

    def test_changed_head_creates_one_meaningful_delta_and_replays(self):
        self.first()
        client = FixtureClient(cache=self.cache(), heads={"example/alpha": NEW_HEAD, "example/empty": None})
        client.rows[0]["pushed_at"] = LATER
        receipt = scrape(self.root, client=client, now=LATER)
        self.assertEqual(receipt["semantic_changes"], 1)
        event = load_json(self.root / "data" / "observations" / "0000000002.json")
        self.assertEqual(event["changes"][0]["after"]["observed_head"], NEW_HEAD)
        self.assertEqual(event["changes"][0]["repository_id"], 111)
        bundle = validate(self.root)
        self.assertEqual(state_digest(bundle["state"]), receipt["snapshot_sha256"])
        result = build(self.root, self.root / "site")
        relation = query(self.root / "site" / "catalog.sqlite",
                         "SELECT captured_commit, observed_public_head, head_relation FROM repo_observations WHERE repository_id=111")
        self.assertEqual(relation["rows"], [(HEAD, NEW_HEAD, "different_from_carried_snapshot")])
        self.assertEqual(query(self.root / "site" / "catalog.sqlite",
                               "SELECT count(*) FROM change_observations WHERE sequence=2")["rows"], [(1,)])
        self.assertGreater(result["database_bytes"], 0)

    def test_self_generated_push_and_size_do_not_churn(self):
        self.first()
        before = self.public_hashes()
        client = FixtureClient(cache=self.cache())
        client.rows[2].update(pushed_at=LATER, size=123456, updated_at=LATER)
        client.heads["example/catalog"] = NEW_HEAD
        result = scrape(self.root, client=client, now=LATER)
        self.assertEqual(result["semantic_changes"], 0)
        self.assertEqual(before, self.public_hashes())
        self.assertFalse(any("/repos/example/catalog/git/ref/" in path for path, _ in client.calls))

    def test_pushed_timestamp_without_head_change_is_not_semantic(self):
        self.first()
        before = self.public_hashes()
        client = FixtureClient(cache=self.cache())
        client.rows[0]["pushed_at"] = LATER
        result = scrape(self.root, client=client, now=LATER)
        self.assertEqual(result["semantic_changes"], 0)
        self.assertEqual(before, self.public_hashes())

    def test_http_parse_rate_limit_and_redirect_fail_preserving_last_good(self):
        self.first()
        before = self.public_hashes()
        cases = [
            (500, {}, b"upstream diagnostics"),
            (403, {"x-ratelimit-remaining": "0"}, b""),
            (429, {}, b""),
            (404, {}, b""),
            (301, {"location": "https://github.com/other/name"}, b""),
            (200, {}, b'{"broken":'),
            (200, {}, b'{"truncated":true}'),
            (200, {}, b'{"private":true,"private":false}'),
            (200, {}, b'{"public_repos":NaN}'),
        ]
        for response in cases:
            with self.subTest(status=response[0], body=response[2][:20]):
                client = FixtureClient(cache=self.cache(),
                                       override=lambda obj, path, headers: response if obj.requests == 3 else None)
                with self.assertRaises(CatalogError):
                    scrape(self.root, client=client, now=LATER)
                self.assertEqual(before, self.public_hashes())
                receipt = load_json(self.root / ".cache" / "last-run.json")
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["last_success_at"], NOW)

    def test_valid_json_truncated_page_is_not_deletion(self):
        self.first()
        before = self.public_hashes()

        def truncated(client, path, headers):
            if "/repos?" in path:
                return 200, {}, canonical(client.rows[:1])
            return None

        with self.assertRaisesRegex(CatalogError, "github_incomplete_repository_page"):
            scrape(self.root, client=FixtureClient(cache=self.cache(), override=truncated), now=LATER)
        self.assertEqual(before, self.public_hashes())

    def test_public_page_rejects_private_rows_and_foreign_owners(self):
        self.first()
        before = self.public_hashes()
        for fields in ({"private": True}, {"visibility": "private"},
                       {"full_name": "elsewhere/alpha"}):
            with self.subTest(fields=fields):
                client = FixtureClient(cache=self.cache())
                client.rows[0].update(fields)
                with self.assertRaises(CatalogError):
                    scrape(self.root, client=client, now=LATER)
                self.assertEqual(before, self.public_hashes())

    def test_legitimate_dot_prefixed_public_repository_is_not_omitted(self):
        client = FixtureClient()
        client.rows.append(raw_repo(888, ".github"))
        scrape(self.root, client=client, now=NOW)
        row = validate(self.root)["state"][888]
        self.assertEqual(row["full_name"], "example/.github")
        self.assertEqual(row["availability"], "public")

    def test_complete_missing_source_has_explicit_unavailable_state(self):
        self.first()
        client = FixtureClient(cache=self.cache())
        client.rows = [row for row in client.rows if row["id"] != 111]
        scrape(self.root, client=client, now=LATER)
        state = validate(self.root)["state"]
        self.assertEqual(state[111]["availability"], "not_listed_public")
        self.assertEqual(state[111]["observed_head"], HEAD)
        build(self.root, self.root / "site")
        observed = query(self.root / "site" / "catalog.sqlite",
                         "SELECT observed_public_head, last_known_public_head, head_relation FROM repo_observations WHERE repository_id=111")
        self.assertEqual(observed["rows"], [(None, HEAD, "unavailable")])

    def test_rename_preserves_id_previous_names_and_remains_noop_next_poll(self):
        self.first()
        client = FixtureClient(cache=self.cache())
        client.rows[0].update(full_name="example/renamed", html_url="https://github.com/example/renamed")
        scrape(self.root, client=client, now=LATER)
        state = validate(self.root)["state"][111]
        self.assertEqual(state["previous_names"], ["example/alpha"])
        self.assertEqual(state["availability"], "renamed_public")
        before = self.public_hashes()
        repeated = FixtureClient(cache=self.cache(), rows=client.rows)
        result = scrape(self.root, client=repeated, now="2026-01-02T02:00:00Z")
        self.assertEqual(result["semantic_changes"], 0)
        self.assertEqual(before, self.public_hashes())

    def test_unchanged_metadata_head_cache_expires(self):
        self.first()
        client = FixtureClient(cache=self.cache(), heads={"example/alpha": NEW_HEAD, "example/empty": None})
        result = scrape(self.root, client=client, now="2026-01-03T01:00:00Z")
        self.assertEqual(result["semantic_changes"], 1)
        self.assertTrue(any("/git/ref/" in path for path, _ in client.calls))

    def test_force_head_revalidation_ignores_cache_age(self):
        self.first()
        client = FixtureClient(cache=self.cache(), heads={"example/alpha": NEW_HEAD, "example/empty": None})
        result = scrape(self.root, client=client, now=LATER, refresh_heads=True)
        self.assertEqual(result["semantic_changes"], 1)

    def test_listing_mutation_between_complete_traversals_is_rejected(self):
        self.first()
        before = self.public_hashes()
        bundle = validate(self.root)

        def unstable(client, path, headers):
            if "/repos?" in path and client.listings == 1:
                client.rows[0]["pushed_at"] = LATER
            return None

        with self.assertRaisesRegex(CatalogError, "github_unstable_listing"):
            poll(FixtureClient(cache=self.cache(), override=unstable),
                 bundle["config"], bundle["state"], LATER)
        self.assertEqual(before, self.public_hashes())

    def test_moving_listing_converges_without_reprobing_unchanged_heads(self):
        def moves_once(client, path, headers):
            if "/repos?" in path and client.listings == 1:
                client.rows[0]["pushed_at"] = LATER
                client.heads["example/alpha"] = NEW_HEAD
            return None

        client = FixtureClient(override=moves_once)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as warnings:
            receipt = scrape(self.root, client=client, now=LATER)
        self.assertEqual(receipt["status"], "success")
        self.assertEqual(client.listings, 4)
        self.assertEqual(client.requests, 11)
        self.assertEqual(sum("/repos/example/alpha/git/ref/" in path for path, _ in client.calls), 2)
        self.assertEqual(sum("/repos/example/empty/git/ref/" in path for path, _ in client.calls), 1)
        self.assertEqual(validate(self.root)["state"][111]["observed_head"], NEW_HEAD)
        self.assertEqual(len(list((self.root / "data/observations").glob("[0-9]*.json"))), 1)
        self.assertEqual([json.loads(line) for line in warnings.getvalue().splitlines()],
                         [{"warning": "github_unstable_listing", "attempt": 1, "max_attempts": 3}])

    def test_persistent_movement_stops_after_three_attempts_without_writing_state(self):
        self.first()
        before, cached = self.public_hashes(), self.cache()

        def always_moves(client, path, headers):
            if "/repos?" in path and client.listings % 2 == 1:
                client.rows[0]["pushed_at"] = f"2026-01-02T01:00:{client.listings:02d}Z"
            return None

        client = FixtureClient(cache=cached, override=always_moves)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as warnings:
            with self.assertRaisesRegex(CatalogError, "github_unstable_listing"):
                scrape(self.root, client=client, now=LATER)
        self.assertEqual(client.listings, 6)
        self.assertEqual(self.public_hashes(), before)
        self.assertEqual(self.cache(), cached)
        receipt = load_json(self.root / ".cache/last-run.json")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["last_success_at"], NOW)
        self.assertEqual([json.loads(line)["attempt"] for line in warnings.getvalue().splitlines()], [1, 2])

    def test_scope_retry_keeps_the_original_request_budget(self):
        before = self.public_hashes()

        def moves_once(client, path, headers):
            if "/repos?" in path and client.listings == 1:
                client.rows[0]["pushed_at"] = LATER
            return None

        client = FixtureClient(override=moves_once)
        client.max_requests = 6
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaisesRegex(CatalogError, "github_request_budget_exhausted"):
                scrape(self.root, client=client, now=LATER)
        self.assertEqual(len(client.calls), 6)
        self.assertEqual(client.listings, 2)
        self.assertEqual(self.public_hashes(), before)

    def test_non_scope_errors_are_not_retried(self):
        self.first()
        before = self.public_hashes()
        with mock.patch("rapp_catalog.scrape.poll", side_effect=CatalogError("github_rate_limited")) as probe:
            with mock.patch("sys.stderr", new_callable=io.StringIO) as warnings:
                with self.assertRaisesRegex(CatalogError, "github_rate_limited"):
                    scrape(self.root, client=FixtureClient(cache=self.cache()), now=LATER)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(warnings.getvalue(), "")
        self.assertEqual(self.public_hashes(), before)

    def test_duplicate_and_missing_pagination_are_errors(self):
        bundle = validate(self.root)
        rows = [raw_repo(i, "repo-" + str(i)) for i in range(1, 102)]
        good = FixtureClient(rows=rows)
        state = poll(good, bundle["config"], bundle["state"], NOW)
        self.assertEqual(sum(row["availability"] == "public" for row in state.values()), 101)
        rows[-1] = copy.deepcopy(rows[0])
        with self.assertRaisesRegex(CatalogError, "github_duplicate_repository"):
            poll(FixtureClient(rows=rows), bundle["config"], bundle["state"], NOW)

    def test_unsafe_next_page_and_request_budget_fail(self):
        def poisoned(client, path, headers):
            if "/repos?" in path:
                return 200, {"link": '<https://example.net/steal>; rel="next"'}, canonical(client.rows)
            return None
        before = self.public_hashes()
        with self.assertRaisesRegex(CatalogError, "invalid_pagination"):
            scrape(self.root, client=FixtureClient(override=poisoned), now=NOW)
        limited = FixtureClient()
        limited.max_requests = 1
        with self.assertRaisesRegex(CatalogError, "github_request_budget_exhausted"):
            scrape(self.root, client=limited, now=NOW)
        self.assertEqual(before, self.public_hashes())

    def test_numeric_pagination_alias_must_match_the_public_owner_id(self):
        def wrong_owner(client, path, headers):
            if "/repos?" in path:
                next_path = path.replace("/users/example/repos", "/user/999999/repos")
                next_path = next_path.replace("&page=1", "&page=2")
                return 200, {"link": f'<https://api.github.com{next_path}>; rel="next"'}, canonical(client.rows)
            return None
        before = self.public_hashes()
        with self.assertRaisesRegex(CatalogError, "invalid_pagination"):
            scrape(self.root, client=FixtureClient(override=wrong_owner), now=NOW)
        self.assertEqual(before, self.public_hashes())

    def test_cache_and_projection_do_not_persist_token_or_extra_api_text(self):
        client = FixtureClient()
        ignored = "/" + "Users/synthetic/" + ".copilot/session-state/never-publish"
        client.rows[0]["description"] = ignored
        scrape(self.root, client=client, now=NOW)
        combined = b"".join(path.read_bytes() for path in self.root.rglob("*.json")
                            if ".work" not in path.relative_to(self.root).parts)
        self.assertNotIn(ignored.encode(), combined)
        self.assertNotIn(b"synthetic-test-token", combined)

    def test_receipt_io_failure_cannot_publish_event(self):
        before = self.public_hashes()
        from rapp_catalog.scrape import atomic_json

        def fail_receipt(path, value):
            if Path(path).name == "last-run.json" and value["status"] == "success":
                raise OSError("synthetic")
            return atomic_json(path, value)

        with mock.patch("rapp_catalog.scrape.atomic_json", side_effect=fail_receipt):
            with self.assertRaisesRegex(CatalogError, "scrape_local_error"):
                scrape(self.root, client=FixtureClient(), now=NOW)
        self.assertEqual(before, self.public_hashes())

    def test_local_writer_lock_fails_closed(self):
        lock = self.root / ".cache" / "scrape.lock"
        lock.parent.mkdir()
        lock.write_bytes(b"")
        with self.assertRaisesRegex(CatalogError, "scraper_already_running"):
            self.first()
        self.assertTrue(lock.exists())

    def test_receipt_path_cannot_overwrite_public_source(self):
        before = self.public_hashes()
        with self.assertRaisesRegex(CatalogError, "unsafe_cache_output"):
            scrape(self.root, run_path=self.root / "data" / "distribution.json",
                   client=FixtureClient(), now=NOW)
        self.assertEqual(before, self.public_hashes())

    def test_unexpected_304_and_malformed_head_are_errors(self):
        for response in ((304, {}, b""), (200, {}, canonical({"ref": "bad", "object": {}}))):
            client = FixtureClient(override=lambda obj, path, headers: response if "/git/ref/" in path else None)
            with self.assertRaises(CatalogError):
                scrape(self.root, client=client, now=NOW)
        self.assertEqual(len(list((self.root / "data" / "observations").glob("[0-9]*.json"))), 0)

    def test_real_transport_detects_content_length_truncation(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.code = 200
        response.headers.items.return_value = [("Content-Length", "99")]
        response.read.return_value = b"{}"
        client = GitHubClient()
        client.opener = mock.Mock()
        client.opener.open.return_value = response
        with self.assertRaisesRegex(CatalogError, "truncated_http_body"):
            client.get("/users/example", lambda data: data)
        response.read.side_effect = http.client.IncompleteRead(b"{", 99)
        with self.assertRaisesRegex(CatalogError, "truncated_http_body"):
            client.get("/users/example", lambda data: data)
