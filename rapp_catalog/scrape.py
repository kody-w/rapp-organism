"""Public GitHub polling with complete-response fences and last-good semantics."""

import copy
import datetime as dt
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from .common import (
    CatalogError, atomic_json, boolean, branch_name, decode_json, enum, exact_keys, hex_id,
    integer, license_id, load_json, public_repo_url, public_text, read_config,
    repo_name, require, timestamp, utc_now,
)
from .model import read_generations
from .observations import (
    append_event, initial_state, replay_events, state_digest, validate_observation,
)


API = "https://api.github.com"
MAX_BODY = 8 * 1024 * 1024
HEAD_RECHECK_SECONDS = 24 * 3600


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _next_link(headers, path, pagination_alias=None):
    link = headers.get("link", "")
    require(len(link) < 8192, "invalid_pagination")
    matches = re.findall(r'<([^>]+)>;\s*rel="next"', link)
    require(len(matches) <= 1, "invalid_pagination")
    if not matches:
        require('rel="next"' not in link, "invalid_pagination")
        return False
    parsed = urllib.parse.urlsplit(matches[0])
    current = urllib.parse.urlsplit(API + path)
    query = urllib.parse.parse_qs(current.query, strict_parsing=True)
    require(query.get("page") and len(query["page"]) == 1, "invalid_pagination")
    query["page"] = [str(int(query["page"][0]) + 1)]
    allowed_paths = {current.path}
    if pagination_alias is not None:
        require(re.fullmatch(r"/user/[1-9][0-9]*/repos", pagination_alias), "invalid_pagination")
        allowed_paths.add(pagination_alias)
    require(parsed.scheme == "https" and parsed.netloc == "api.github.com"
            and parsed.path in allowed_paths and not parsed.fragment
            and urllib.parse.parse_qs(parsed.query, strict_parsing=True) == query,
            "invalid_pagination")
    return True


class GitHubClient:
    def __init__(self, cache=None, token=None, max_requests=1000):
        self.cache = copy.deepcopy(cache or {"responses": {}, "heads": {}})
        exact_keys(self.cache, {"responses", "heads"})
        require(isinstance(self.cache["responses"], dict) and isinstance(self.cache["heads"], dict),
                "invalid_http_cache")
        self.token = token
        self.max_requests = integer(max_requests, 1)
        self.requests = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, path, headers):
        request = urllib.request.Request(API + path, headers=headers)
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
            raise CatalogError("github_network_error") from error
        with response:
            status = response.code
            normalized = {key.lower(): value for key, value in response.headers.items()}
            if status not in (200, 304):
                return status, normalized, b""
            try:
                body = response.read(MAX_BODY + 1)
            except http.client.IncompleteRead as error:
                raise CatalogError("truncated_http_body") from error
            except (OSError, TimeoutError, http.client.HTTPException) as error:
                raise CatalogError("github_network_error") from error
            require(len(body) <= MAX_BODY, "github_response_too_large")
            if status == 200 and normalized.get("content-length") is not None:
                require(normalized["content-length"].isdigit()
                        and len(body) == int(normalized["content-length"]),
                        "truncated_http_body")
            return status, normalized, body

    def get(self, path, project, *, pagination_alias=None):
        require(isinstance(path, str) and path.startswith(("/users/", "/repos/"))
                and "://" not in path and "\n" not in path, "invalid_api_path")
        self.requests += 1
        require(self.requests <= self.max_requests, "github_request_budget_exhausted")
        cache_key = hashlib.sha256(path.encode("ascii")).hexdigest()
        cached = self.cache["responses"].get(cache_key)
        headers = {
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "rapp-public-catalog/1",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if cached:
            exact_keys(cached, {"etag", "data", "has_next"})
            require(isinstance(cached["etag"], str)
                    and re.fullmatch(r'(?:W/)?"[A-Za-z0-9_.:/=-]{1,240}"', cached["etag"]),
                    "invalid_etag")
            boolean(cached["has_next"])
            headers["If-None-Match"] = cached["etag"]
        status, response_headers, body = self._request(path, headers)
        if status == 304:
            require(cached is not None, "unexpected_not_modified")
            data, has_next = cached["data"], cached["has_next"]
        elif status == 200:
            try:
                data = decode_json(body)
            except (ValueError, UnicodeError) as error:
                raise CatalogError("github_invalid_json") from error
            try:
                has_next = _next_link(response_headers, path, pagination_alias)
            except (ValueError, TypeError) as error:
                raise CatalogError("invalid_pagination") from error
        elif status in (301, 302, 303, 307, 308):
            raise CatalogError("github_source_redirected")
        elif status == 429 or (status == 403 and response_headers.get("x-ratelimit-remaining") == "0"):
            raise CatalogError("github_rate_limited")
        elif status == 404:
            raise CatalogError("github_source_unavailable")
        elif status == 409:
            raise CatalogError("github_empty_repository")
        elif status == 403:
            raise CatalogError("github_access_denied")
        elif status >= 500:
            raise CatalogError("github_server_error")
        else:
            raise CatalogError("github_http_error")
        try:
            result = project(data)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise CatalogError("github_invalid_response") from error
        etag = response_headers.get("etag")
        if status == 200 and etag is not None:
            require(re.fullmatch(r'(?:W/)?"[A-Za-z0-9_.:/=-]{1,240}"', etag),
                    "invalid_etag")
            self.cache["responses"][cache_key] = {"etag": etag, "data": result, "has_next": has_next}
        elif status == 200:
            self.cache["responses"].pop(cache_key, None)
        return result, has_next


def project_profile(value, owner):
    require(isinstance(value, dict), "github_invalid_profile")
    require(repo_name(value["login"], full=False).lower() == owner.lower(), "github_owner_changed")
    return {"login": value["login"], "id": integer(value["id"], 1),
            "public_repos": integer(value["public_repos"])}


def project_repositories(value, owner, observer):
    require(isinstance(value, list) and len(value) <= 100, "github_invalid_repository_page")
    result = []
    for row in value:
        require(isinstance(row, dict) and row["private"] is False
                and row.get("visibility", "public") == "public", "github_nonpublic_repository")
        name = repo_name(row["full_name"])
        require(name.split("/")[0].lower() == owner.lower(), "github_owner_changed")
        url = public_repo_url(row["html_url"])
        require(url == "https://github.com/" + name, "github_repository_url_mismatch")
        is_observer = name.lower() == observer.lower()
        license_data = row["license"]
        spdx = license_id(license_data["spdx_id"]) if license_data is not None else None
        result.append({
            "id": integer(row["id"], 1), "full_name": name, "html_url": url,
            "private": False, "visibility": "public",
            "default_branch": branch_name(row["default_branch"], nullable=True),
            "archived": boolean(row["archived"]), "fork": boolean(row["fork"]),
            "license": {"spdx_id": spdx} if spdx is not None else None,
            "pushed_at": None if is_observer or row["pushed_at"] is None
            else timestamp(row["pushed_at"]),
            "size": None if is_observer else integer(row["size"]),
        })
    return result


def complete_listing(client, config):
    owner = config["owner"]
    profile, _ = client.get("/users/" + owner, lambda value: project_profile(value, owner))
    count = profile["public_repos"]
    require(count <= 10000, "github_scope_too_large")
    pages = max(1, (count + 99) // 100)
    repos = {}
    for page in range(1, pages + 1):
        path = f"/users/{owner}/repos?type=owner&sort=full_name&direction=asc&per_page=100&page={page}"
        rows, has_next = client.get(
            path, lambda value: project_repositories(value, owner, config["observer_repository"]),
            pagination_alias=f"/user/{profile['id']}/repos")
        require(has_next == (page < pages), "github_incomplete_pagination")
        require(len(rows) == (100 if page < pages else count - 100 * (pages - 1)),
                "github_incomplete_repository_page")
        for row in rows:
            require(row["id"] not in repos, "github_duplicate_repository")
            repos[row["id"]] = row
    require(len(repos) == count, "github_repository_count_mismatch")
    require(len({row["full_name"].lower() for row in repos.values()}) == count,
            "github_duplicate_repository_name")
    return profile, repos


def resolve_head(client, row, now, force=False):
    branch = row["default_branch"]
    require(branch is not None, "github_unknown_default_branch")
    signature = {key: row[key] for key in ("full_name", "default_branch", "pushed_at")}
    cached = client.cache["heads"].get(str(row["id"]))
    if cached is not None:
        exact_keys(cached, {"signature", "verified_at", "head", "availability"})
        age = (dt.datetime.fromisoformat(now.replace("Z", "+00:00")) -
               dt.datetime.fromisoformat(timestamp(cached["verified_at"]).replace("Z", "+00:00"))
               ).total_seconds()
        hex_id(cached["head"], nullable=True)
        enum(cached["availability"], {"public", "empty"})
        require((cached["head"] is None) == (cached["availability"] == "empty"),
                "invalid_head_cache")
        if not force and cached["signature"] == signature and 0 <= age < HEAD_RECHECK_SECONDS:
            return cached["head"], cached["availability"]
    wanted = "refs/heads/" + branch

    def project_ref(value):
        require(isinstance(value, dict) and value["ref"] == wanted
                and value["object"]["type"] == "commit", "github_invalid_branch_ref")
        return {"ref": wanted, "object": {"type": "commit", "sha": hex_id(value["object"]["sha"])}}

    path = "/repos/" + row["full_name"] + "/git/ref/heads/" + urllib.parse.quote(branch, safe="")
    try:
        response, _ = client.get(path, project_ref)
        head, availability = response["object"]["sha"], "public"
    except CatalogError as error:
        if error.code == "github_empty_repository" and row["size"] == 0:
            head, availability = None, "empty"
        else:
            raise
    client.cache["heads"][str(row["id"])] = {
        "signature": signature, "verified_at": now, "head": head, "availability": availability,
    }
    return head, availability


def poll(client, config, previous, now, refresh_heads=False):
    """No writes. A failing request or unstable traversal discards the candidate."""
    now = timestamp(now)
    profile, listing = complete_listing(client, config)
    candidate = {}
    for rid in sorted(listing):
        row = listing[rid]
        old = previous.get(rid)
        name = row["full_name"]
        old_names = set(old["previous_names"]) if old else set()
        if old and old["full_name"] != name:
            old_names.add(old["full_name"])
        old_names.discard(name)
        if name.lower() == config["observer_repository"].lower():
            head, availability = None, "observer_excluded"
        else:
            head, availability = resolve_head(client, row, now, refresh_heads)
            if old_names:
                availability = "renamed_public"
        result = {
            "repository_id": rid, "full_name": name, "public_url": row["html_url"],
            "default_branch": row["default_branch"], "observed_head": head,
            "availability": availability, "previous_names": sorted(old_names),
            "archived": row["archived"], "fork": row["fork"],
            "license_spdx": row["license"]["spdx_id"] if row["license"] else None,
        }
        candidate[rid] = validate_observation(result)
    final_profile, final_listing = complete_listing(client, config)
    require(profile == final_profile and listing == final_listing, "github_unstable_listing")
    for rid, row in previous.items():
        if rid not in candidate:
            candidate[rid] = {**row, "availability": "not_listed_public"}
    return candidate


def scrape(root, cache_path=None, run_path=None, *, client=None, now=None,
           refresh_heads=False, max_requests=1000):
    root = Path(root)
    config = read_config(root)
    now = timestamp(now or utc_now())
    cache_path = Path(cache_path) if cache_path else root / ".cache" / "github.json"
    run_path = Path(run_path) if run_path else root / ".cache" / "last-run.json"
    cache_directory = root / ".cache"
    require(not cache_directory.is_symlink(), "unsafe_cache_directory")
    for path in (cache_path, run_path):
        require(path.resolve().parent == cache_directory.resolve()
                and path.suffix == ".json" and not path.is_symlink(), "unsafe_cache_output")
    require(cache_path.resolve() != run_path.resolve(), "overlapping_cache_outputs")
    lock = root / ".cache" / "scrape.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise CatalogError("scraper_already_running") from error
    os.close(handle)
    last_success = None
    current_digest = None
    try:
        if run_path.exists():
            old_run = load_json(run_path)
            if old_run.get("last_success_at"):
                last_success = timestamp(old_run["last_success_at"])
        generations = read_generations(root)
        baseline = initial_state(root, generations)
        previous, events = replay_events(root, baseline)
        current_digest = state_digest(previous)
        if client is None:
            cached = load_json(cache_path) if cache_path.exists() else None
            client = GitHubClient(cached, os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
                                  max_requests=max_requests)
        candidate = poll(client, config, previous, now, refresh_heads)
        # Everything that may fail validation runs before the sole tracked write.
        for row in candidate.values():
            validate_observation(row)
        result_digest = state_digest(candidate)
        if events:
            require(now >= events[-1]["observed_at"], "observation_clock_regression")
        atomic_json(cache_path, client.cache)
        result = {
            "schema": "rapp-public-poll-receipt/1", "status": "success",
            "checked_at": now, "last_success_at": now, "snapshot_sha256": result_digest,
            "semantic_changes": sum(previous.get(rid) != row for rid, row in candidate.items()),
            "requests": client.requests, "error": None,
        }
        atomic_json(run_path, result)
        # The tracked event is the final operation. A receipt/cache write failure
        # cannot leave a new tracked snapshot behind while reporting a failed poll.
        append_event(root, previous, candidate, events, now)
        return result
    except (CatalogError, OSError, ValueError, TypeError, KeyError) as error:
        code = error.code if isinstance(error, CatalogError) else "scrape_local_error"
        failure = {
            "schema": "rapp-public-poll-receipt/1", "status": "failed",
            "checked_at": now, "last_success_at": last_success,
            "snapshot_sha256": current_digest, "semantic_changes": 0,
            "requests": client.requests if client else 0, "error": code,
        }
        atomic_json(run_path, failure)
        raise CatalogError(code) from error
    finally:
        lock.unlink(missing_ok=True)
