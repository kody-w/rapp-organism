"""Small, explicit publication contracts shared by the importer and scraper."""

import base64
import binascii
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid


class CatalogError(Exception):
    """A bounded diagnostic code, never an upstream response or local path."""

    def __init__(self, code):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            code = "invalid_diagnostic"
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise CatalogError(code)


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def decode_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def invalid_number(_value):
        raise CatalogError("invalid_json_number")

    try:
        return json.loads(body, object_pairs_hook=unique, parse_constant=invalid_number)
    except (ValueError, UnicodeError) as exc:
        raise CatalogError("invalid_json") from exc


def load_json(path):
    return decode_json(Path(path).read_bytes())


def exact_keys(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys), "unexpected_fields")


def integer(value, minimum=0):
    require(type(value) is int and value >= minimum, "invalid_integer")
    return value


def boolean(value):
    require(type(value) is bool, "invalid_boolean")
    return value


def hex_id(value, length=40, nullable=False):
    if value is None and nullable:
        return None
    require(isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value),
            "invalid_hash")
    return value


def enum(value, choices):
    require(isinstance(value, str) and value in choices, "unknown_status")
    return value


def public_text(value, maximum=4096):
    require(isinstance(value, str) and len(value) <= maximum, "invalid_text")
    lowered = value.lower()
    forbidden = (
        "." + "copilot/session-state", "file" + "://",
        "-----begin " + "private key", "-----begin " + "rsa private key",
        "authorization" + ": bearer", "github" + "_pat_",
    )
    require(not any(s in lowered for s in forbidden), "nonpublic_text")
    require(not re.search(r"""(?:^|[\s"'=(])/(?:users|home|private)/""", lowered),
            "nonpublic_text")
    require(not re.search(r"\bgh[pousr]_[A-Za-z0-9]{20,}", value), "credential_text")
    require(not re.search(r"\bAKIA[A-Z0-9]{16}\b", value), "credential_text")
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "invalid_unicode")
    return value


def repo_name(value, full=True):
    public_text(value, 250)
    pattern = r"[A-Za-z0-9_.-]{1,100}"
    require(re.fullmatch(pattern + ("/" + pattern if full else ""), value),
            "invalid_repository_name")
    require(all(part not in {".", ".."} for part in value.split("/")), "invalid_repository_name")
    return value


def public_repo_url(value):
    public_text(value, 512)
    prefix = "https://github.com/"
    require(value.startswith(prefix), "nonpublic_url")
    name = value[len(prefix):]
    if name.endswith(".git"):
        name = name[:-4]
    repo_name(name)
    return prefix + name


def branch_name(value, nullable=False):
    if value is None and nullable:
        return None
    public_text(value, 255)
    require(value and not value.startswith(("/", "-")) and not value.endswith(("/", ".")),
            "invalid_branch")
    require(not any(x in value for x in ("..", "@{", "//", "\\", " ", ":", "?", "*", "[")),
            "invalid_branch")
    require(not any(ord(c) < 33 or ord(c) == 127 for c in value), "invalid_branch")
    return value


def license_id(value):
    if value is None:
        return None
    public_text(value, 80)
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,79}", value),
            "invalid_license_identifier")
    return value


def timestamp(value):
    public_text(value, 40)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogError("invalid_timestamp") from exc
    require(parsed.tzinfo is not None, "timestamp_without_timezone")
    return parsed.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def path_fields(encoded):
    require(isinstance(encoded, str) and len(encoded) <= 22000, "invalid_path_encoding")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CatalogError("invalid_path_encoding") from exc
    require(base64.b64encode(raw).decode("ascii") == encoded, "noncanonical_path_encoding")
    require(raw and len(raw) <= 16384 and b"\0" not in raw, "invalid_path")
    require(not raw.startswith((b"/", b"\\")), "absolute_path")
    require(not re.match(rb"^[A-Za-z]:", raw), "absolute_path")
    require(all(p not in (b"", b".", b"..") for p in raw.split(b"/")), "unsafe_path")
    # Backslashes are legal Git filename bytes, not host filesystem separators.
    # No projected filename is ever opened, joined onto a directory, or extracted.
    text = raw.decode("utf-8", errors="replace")
    public_text(text, 16384)
    require(not re.search(r"[A-Za-z]:[\\/]", text), "absolute_path")
    try:
        display = raw.decode("utf-8")
    except UnicodeDecodeError:
        display = None
    return display, encoded


def relative_name(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]*\.jsonl", value),
            "invalid_partition_name")
    return value


def generation_id(value):
    require(isinstance(value, str) and re.fullmatch(r"g[0-9]{4,8}", value),
            "invalid_generation")
    return value


def safe_tree(value):
    if isinstance(value, str):
        public_text(value, 20000)
    elif isinstance(value, dict):
        for key, item in value.items():
            public_text(key, 100)
            safe_tree(item)
    elif isinstance(value, list):
        for item in value:
            safe_tree(item)
    else:
        require(value is None or type(value) in (int, bool), "invalid_public_value")


def atomic_bytes(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), "symlink_output")
    stage = path.parent / (".write-" + uuid.uuid4().hex)
    try:
        with stage.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


def atomic_json(path, value):
    safe_tree(value)
    atomic_bytes(path, canonical(value))


@contextlib.contextmanager
def workspace(parent, prefix):
    root = Path(parent) / ".work"
    root.mkdir(parents=True, exist_ok=True)
    require(not root.is_symlink(), "symlink_output")
    directory = root / (prefix + "-" + uuid.uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        if directory.exists():
            shutil.rmtree(directory)


def replace_directory(stage, target):
    target = Path(target)
    require(not target.is_symlink(), "symlink_output")
    backup = stage.parent / ("previous-" + uuid.uuid4().hex)
    had_target = target.exists()
    if had_target:
        target.rename(backup)
    try:
        stage.rename(target)
    except OSError:
        if had_target:
            backup.rename(target)
        raise
    if had_target:
        shutil.rmtree(backup)


def read_config(root):
    path = Path(root) / "catalog.json"
    require(not path.is_symlink(), "symlink_input")
    value = load_json(path)
    exact_keys(value, ("schema", "owner", "observer_repository", "site_url",
                       "stale_after_seconds"))
    require(value["schema"] == "rapp-public-catalog-config/1", "config_schema")
    repo_name(value["owner"], full=False)
    repo_name(value["observer_repository"])
    require(value["observer_repository"].split("/")[0].lower() == value["owner"].lower(),
            "observer_owner_mismatch")
    require(re.fullmatch(r"https://[A-Za-z0-9-]+\.github\.io/[A-Za-z0-9_.-]+/",
                         value["site_url"]), "invalid_site_url")
    integer(value["stale_after_seconds"], 60)
    return value
