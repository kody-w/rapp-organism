import base64
import collections
import copy
import hashlib
from pathlib import Path
import shutil
import unittest
import urllib.parse

from rapp_catalog.common import canonical, file_hash, load_json, workspace
from rapp_catalog.scrape import GitHubClient
from rapp_catalog.seed import import_seed


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-01-02T00:00:00Z"
LATER = "2026-01-02T01:00:00Z"
HEAD = "1" * 40
NEW_HEAD = "2" * 40
XSS_PATH = "src/<img src=x onerror=alert(1)>.py"


def entry(path, obj="a" * 40, mode="100644"):
    raw = path if isinstance(path, bytes) else path.encode()
    return {"mode": mode, "type": "commit" if mode == "160000" else "blob",
            "git_object": obj, "path_bytes_base64": base64.b64encode(raw).decode(),
            "path": raw.decode("utf-8", "replace")}


def sources():
    owner_entries = [
        entry("README.md"), entry(XSS_PATH), entry(b"raw-\xff.txt"),
        entry("link", mode="120000"), entry("dependency", "b" * 40, "160000"),
        entry("missing", "c" * 40, "160000"),
    ]
    dep_entries = [entry("library.py", "d" * 40)]
    scope_rows = []
    genome_rows = []
    measured_rows = []
    for rid, name, commit, entries in ((111, "alpha", HEAD, owner_entries),
                                      (222, "empty", None, [])):
        scope_rows.append({
            "repository_id": rid, "name": name, "public_url": "https://github.com/example/" + name,
            "git_url": "https://github.com/example/" + name + ".git",
            "captured_default_branch": "main", "captured_commit": commit,
            "rapp_metadata_match": rid == 111, "archived": False, "fork": False,
            "license_detection": "MIT" if rid == 111 else None,
        })
        genome_rows.append({
            "repository_id": rid, "repo": name, "public_url": scope_rows[-1]["public_url"],
            "source_snapshot_commit": commit, "default_branch": "main",
            "license_detection": scope_rows[-1]["license_detection"],
            "entries": entries, "empty": commit is None, "lfs_objects": [],
        })
        measured_rows.append({
            "repository_id": rid, "repository": name, "public_url": scope_rows[-1]["public_url"],
            "captured_commit": commit, "captured_default_branch": "main",
            "archived": False, "fork": False, "empty": commit is None,
            "tracked_entries": len(entries), "entry_modes": dict(collections.Counter(x["mode"] for x in entries)),
            "entry_coverage": {"scanned_utf8": 3, "symlink_target_not_dereferenced": 1,
                               "gitlink_not_an_owner_blob": 2} if entries else {},
            "observed_rapp_evidence": bool(entries), "compatibility": "not_established",
            "entry_binding_sha256": hashlib.sha256(canonical(entries)).hexdigest(),
            "artifact_outcomes": {"frame:refused": 1, "frame:unverified_history": 2} if entries else {},
            "artifact_declaration_outcomes": {
                "declared_legacy_rapp:frame:refused": 1,
                "declared_rapp1:frame:unverified_history": 2,
            } if entries else {},
            "stream_checks": [],
        })
    scope = {"schema": "rapp-public-source-scope/1", "created_at": "2026-01-01T00:00:00Z",
             "owner": "example", "public_repositories": 2, "repositories": scope_rows}
    genome = {
        "schema": "rapp-full-genome-index/1", "repositories": genome_rows,
        "source_scope": copy.deepcopy(scope),
        "counts": {"repositories": 2, "tracked_entries": 6, "unique_blobs": 1, "source_blob_bytes": 9},
    }
    links = [
        {"parent_repo": "alpha", "path": "dependency", "commit": "b" * 40,
         "url": "https://github.com/other/library.git", "status": "hydrated"},
        {"parent_repo": "alpha", "path": "missing", "commit": "c" * 40,
         "url": None, "status": "pre-existing-unavailable"},
    ]
    dependencies = {
        "schema": "rapp-public-gitlink-bodies/1", "links": copy.deepcopy(links),
        "dependencies": [{"source_url": "https://github.com/other/library.git", "commit": "b" * 40,
                          "entries": dep_entries}], "extra_blob_count": 1, "unavailable_count": 1,
    }
    matrix = {
        "schema": "rapp-captured-estate-inventory/1", "report_status": "complete",
        "compatibility_verdict": "not_established", "capture_errors": [],
        "repositories": measured_rows, "dependency_links": links,
        "canonical": {"anchor_selected_commit": "e" * 40, "revision": "rev-15"},
        "dependencies": [{
            "source_url": "https://github.com/other/library.git", "captured_commit": "b" * 40,
            "tracked_entries": 1, "observed_rapp_evidence": False, "compatibility": "not_established",
            "entry_binding_sha256": hashlib.sha256(canonical(dep_entries)).hexdigest(),
            "entry_coverage": {"scanned_utf8": 1}, "entry_modes": {"100644": 1},
            "artifact_outcomes": {}, "artifact_declaration_outcomes": {}, "stream_checks": [],
        }],
        "blobs": [
            {"git_object": "a" * 40, "sha256": "a" * 64, "bytes": 9, "bytes_hashed": 9,
             "content_status": "scanned_utf8", "integrity": "verified_git_blob", "references": 4},
            {"git_object": "d" * 40, "sha256": "d" * 64, "bytes": 3, "bytes_hashed": 3,
             "content_status": "scanned_utf8", "integrity": "verified_git_blob", "references": 1},
        ],
        "coverage": {
            "owner_repositories_expected": 2, "owner_repositories_accounted": 2,
            "owner_tracked_entries_expected": 6, "owner_tracked_entries_accounted": 6,
            "owner_unique_blobs": 1, "owner_unique_blob_bytes_stat": 9,
            "dependency_bodies_accounted": 1, "dependency_tracked_entries_accounted": 1,
            "owner_and_dependency_unique_blobs": 2, "object_store_file_names": 2,
            "bytes_hashed": 12, "gitlinks": 2, "gitlinks_unavailable": 1,
            "blob_integrity": {"verified_git_blob": 2}, "unique_blob_content_coverage": {"scanned_utf8": 2},
        },
    }
    receipt = {"status": "VERIFIED", "archive": {"sha256": "f" * 64, "bytes": 100},
               "frame_hash": "e" * 64}
    return {"scope": scope, "genome": genome, "dependencies": dependencies,
            "matrix": matrix, "receipt": receipt}


def raw_repo(rid, name, empty=False):
    return {
        "id": rid, "full_name": "example/" + name, "html_url": "https://github.com/example/" + name,
        "private": False, "visibility": "public", "default_branch": "main",
        "archived": False, "fork": False, "license": {"spdx_id": "MIT"},
        "pushed_at": "2026-01-01T01:00:00Z", "size": 0 if empty else 1,
        "description": "Ignored upstream text",
    }


class FixtureClient(GitHubClient):
    def __init__(self, cache=None, rows=None, heads=None, override=None):
        super().__init__(cache=cache, token="synthetic-test-token")
        self.rows = copy.deepcopy(rows if rows is not None else [
            raw_repo(111, "alpha"), raw_repo(222, "empty", True), raw_repo(999, "catalog"),
        ])
        self.heads = dict(heads or {"example/alpha": HEAD, "example/empty": None})
        self.override = override
        self.calls = []
        self.listings = 0

    def _request(self, path, headers):
        self.calls.append((path, dict(headers)))
        if self.override:
            value = self.override(self, path, headers)
            if value is not None:
                return value
        response_headers = {}
        if path == "/users/example":
            data = {"login": "example", "id": 100, "public_repos": len(self.rows)}
        elif path.startswith("/users/example/repos?"):
            self.listings += 1
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["page"][0])
            data = self.rows[(page - 1) * 100:page * 100]
            if page * 100 < len(self.rows):
                next_path = path.replace(f"&page={page}", f"&page={page + 1}")
                next_path = next_path.replace("/users/example/repos", "/user/100/repos")
                response_headers["link"] = f'<https://api.github.com{next_path}>; rel="next"'
        elif path.startswith("/repos/"):
            name = "/".join(path.split("/")[2:4])
            sha = self.heads.get(name, HEAD)
            if sha is None:
                return 409, {}, b""
            ref = urllib.parse.unquote(path.split("/git/ref/heads/", 1)[1])
            data = {"ref": "refs/heads/" + ref, "object": {"type": "commit", "sha": sha}}
        else:
            raise AssertionError("Unexpected test endpoint")
        body = canonical(data)
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        response_headers["etag"] = etag
        if headers.get("If-None-Match") == etag:
            return 304, response_headers, b""
        return 200, response_headers, body


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.work_context = workspace(ROOT, "test")
        self.work = self.work_context.__enter__()
        self.root = self.work / "repo"
        self.root.mkdir()
        self.input_dir = self.work / "inputs"
        self.input_dir.mkdir()
        self.inputs = sources()
        self.paths = {}
        for name, data in self.inputs.items():
            path = self.input_dir / (name + ".json")
            path.write_bytes(canonical(data))
            self.paths[name] = path
        config = {
            "schema": "rapp-public-catalog-config/1", "owner": "example",
            "observer_repository": "example/catalog", "site_url": "https://example.github.io/catalog/",
            "stale_after_seconds": 21600,
        }
        (self.root / "catalog.json").write_bytes(canonical(config))
        import_seed(self.root, **self.paths)
        data = self.root / "data"
        (data / "authority.json").write_bytes((ROOT / "data" / "authority.json").read_bytes())
        distribution = load_json(ROOT / "data" / "distribution.json")
        distribution["generations"][0].update(
            carrier_bytes=100, carrier_sha256="f" * 64, frame_hash="e" * 64,
            publication_status="not_published", screening_status="pending",
            clear_for_exact_publication=False)
        (data / "distribution.json").write_bytes(canonical(distribution))
        (data / "payload-policy.json").write_bytes(canonical({
            "schema": "rapp-public-payload-policy/1",
            "encrypted_full_upload_allowed": False,
            "withheld_payloads": [],
        }))
        shutil.copytree(ROOT / "data" / "workflows", data / "workflows")
        shutil.copyfile(ROOT / "data" / "workflow-integrations.json", data / "workflow-integrations.json")
        observations = data / "observations"
        observations.mkdir()
        baseline = {
            "schema": "rapp-observation-baseline/1", "generation_id": "g0002",
            "generation_manifest_sha256": file_hash(data / "generations" / "g0002" / "manifest.json"),
        }
        (observations / "baseline.json").write_bytes(canonical(baseline))
        shutil.copytree(ROOT / "web", self.root / "web")
        for name in ("README.md", "LICENSE", "DATA_LICENSES.md", "DISTRIBUTION.md", "llms.txt"):
            shutil.copyfile(ROOT / name, self.root / name)

    def tearDown(self):
        self.work_context.__exit__(None, None, None)

    def public_hashes(self):
        return {str(path.relative_to(self.root / "data")): file_hash(path)
                for path in (self.root / "data").rglob("*") if path.is_file()}

    def cache(self):
        return load_json(self.root / ".cache" / "github.json")
