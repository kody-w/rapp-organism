# Public metadata boundary and future payload recovery

**The exact `g0002` full carrier is WITHHELD.**
`clear_for_exact_publication` is false, screening is blocked, and there are no
part URLs or public payload downloads. This is a definitive withholding
decision, not a pending-success state. No raw or encrypted-full upload is an
allowed workaround. The original remains locally retained by its custodian;
this catalog does not move, modify or delete it.

The public artifact is an explicitly allowlisted metadata/query **`projection_of`**
`c1a86a55bece80df11cfc5e747cf016c83d4cf4ad5347dab9f7ce89233d0e58d`
(21,919,507,382 original bytes). Complete frozen-scope metadata is not
availability or clearance of the raw/private payload. Detailed review findings,
sensitive locators, raw diagnostics and source-body excerpts are not published.

The existing index/verifier and recovery tools are retained only for **different,
separately cleared future assets**. No private publisher helper is needed for
such assets. All four carrier commands refuse withheld manifests and identities,
including local `verify-carrier` and external-index attempts to override policy.

**Distribution schema 3** adds the definitive withholding state and explicit
clearance flag while retaining contiguous offsets. SQLite schema 6 exposes
`metadata_payload_boundaries`, `distributions.clear_for_exact_publication`, and
`release_parts.byte_offset`. Authority JSON remains schema 2; its roles are not
changed by payload availability.

## Exact index contract for the publisher

The release's public index can be an exact copy of `data/distribution.json`.
All four transfer commands accept `--manifest PATH` for a downloaded copy of
that index; otherwise they use the repository's tracked index.

Root object, with no additional fields:

```text
{
  "schema": "rapp-public-distribution/3",
  "generations": [carrier, ...]
}
```

Each `carrier` has exactly these fields:

| Field | Contract |
|---|---|
| `generation_id` | Existing ID such as `g0002` |
| `carrier_bytes` | Positive original whole-carrier length |
| `carrier_sha256` | Lowercase 64-hex SHA-256 of the original whole bytes |
| `frame_hash` | Lowercase 64-hex carrier frame identity |
| `publication_status` | `withheld`, `not_published`, `staged`, or `published` |
| `screening_status` | `pending`, `approved`, or `blocked` |
| `clear_for_exact_publication` | Boolean; false for withheld/blocked/pending, true only with approved screening |
| `parts` | Ordered list of 1–1,000 parts when staged/published; empty when withheld/not published |
| `verification` | Null until published; exact verification receipt described below |

Each part has exactly `index`, `offset`, `bytes`, `sha256`, and `url`.

- `index` is an integer beginning at **1**, without gaps, repeats or reordering.
- `offset` is an integer beginning at **0**, measured in the **whole original
  carrier**. It must equal the sum of all preceding part byte lengths.
- `bytes` is positive and **strictly smaller than 2,147,483,648**. Prefer
  **1,073,741,824 bytes (1 GiB)** per full part.
- `sha256` is the lowercase 64-hex SHA-256 of that exact part.
- `url` is the real immutable URL
  `https://github.com/kody-w/rapp-organism/releases/download/TAG/ASSET`.
  Tags and asset filenames use ASCII letters, digits, dots, underscores and
  hyphens; neither segment may be `.` or `..`. No `latest` shortcut, credentials,
  query strings, alternative hosts or private download URLs.
- The final `offset + bytes` must equal `carrier_bytes`. Gaps, overlaps, wrong
  totals and ambiguous ordering are rejected before network access.

Parts are literal consecutive byte ranges of the original carrier, **not**
independently zipped/tarred/transcoded wrappers. Joining them in index order must
reproduce the original bytes exactly. Never overwrite a released part or reuse
its URL for different bytes. Use a new immutable release identity when needed.
GitHub's 1,000-assets-per-release ceiling also counts an uploaded index and
receipts: reserve slots for them (at most 999 parts if one index asset is included).

For a separately cleared future payload, offsets are cumulative byte counts.
There is deliberately no split/upload plan or chunk URL list for the withheld
carrier. `data/payload-policy.json` pins its identity and the minimal public-safe
withholding reason. The protected record cannot be deleted from the main index,
republished through an external index, substituted under its generation ID, or
renamed to evade the hash-based gate. Encryption is explicitly not permitted as
a full-payload publication workaround.

### Verification and publication transitions

**This flow does not apply to the withheld carrier.** It is only for a distinct
future payload whose exact bytes independently clear disclosure and rights
review. Do not edit away the withholding policy or reuse the blocked identity.

1. A future, unreviewed asset starts `not_published`, screening `pending`, clearance
   false, with an empty part list and null verification. A failed definitive gate
   is `withheld`/`blocked`/false, not merely pending.
2. After screening, upload immutable parts to actual **public** version-specific
   release URLs. Draft/authenticated-only assets do not satisfy public availability.
3. Prepare an index with `publication_status: staged`, `screening_status: approved`,
   `clear_for_exact_publication: true`, complete offsets/hashes/URLs, and
   `verification: null`.
4. Run the retained public verifier (it streams the full carrier but saves no body):

   ```sh
   python3 -m rapp_catalog verify-distribution \
     --generation "CLEARED_GENERATION" --manifest downloads/cleared-index.staged.json \
     > downloads/cleared-verification.json
   ```

   `CLEARED_GENERATION` is a non-runnable placeholder: replace it only with an
   actual separately cleared identity. The publisher prepares that staged index
   and its `downloads/` directory. A successful command returns:

   ```text
   {
     "schema": "rapp-distribution-verification/1",
     "method": "downloaded_parts_and_assembled_sha256",
     "descriptor_sha256": "<computed lowercase SHA-256>",
     "carrier_sha256": "<verified original whole SHA-256>",
     "carrier_bytes": <verified original whole length>,
     "verified_at": "<UTC timestamp>"
   }
   ```

5. Put that receipt in `verification`, set `publication_status: published`, and
   publish the final index in Git and optionally as a release asset. The verifier
   does not edit the index or perform uploads. Review/build before publication.

The descriptor hash binds only `generation_id`, `carrier_bytes`,
`carrier_sha256`, `frame_hash`, and `parts`, including every offset and URL.
Its canonical form is Python standard-library JSON with `sort_keys=True`,
`ensure_ascii=True`, `allow_nan=False`, `separators=(",", ":")`, plus one trailing
LF, encoded as ASCII. `sha256(canonical_bytes)` is `descriptor_sha256`.
Status/receipt fields are excluded so a verified staged descriptor can become
published without a self-referential hash. The public `descriptor_hash()`
implementation is in `rapp_catalog/distribution.py`; the verifier generates the
receipt, so upload tooling does not need to duplicate this algorithm.

A receipt is a publisher assertion backed by a reproducible hash check, not
source ratification, a license grant, or runtime activation. Consumers recheck
the actual bytes; no command extracts, imports or executes carrier contents.

## Public download, resume, reassemble and verify

Prerequisites: clone this public repository, Python 3.11+ with stdlib SQLite,
a **separately cleared, published and approved** index, and adequate free space. No token, private
key, original capture folder, external package or paid warehouse is needed.
Choose a filesystem/volume with sufficient space **before cloning**.

The following are templates for future cleared assets, **not functioning download
instructions for `g0002`**. Replace `CLEARED_GENERATION` and `CLEARED_SHA256` only
from an actual cleared index. The CLI default identity is currently withheld.

```sh
python3 -m rapp_catalog download-distribution \
  --generation "CLEARED_GENERATION" --directory downloads/cleared-payload

python3 -m rapp_catalog assemble-distribution \
  --generation "CLEARED_GENERATION" --directory downloads/cleared-payload

python3 -m rapp_catalog verify-carrier --generation "CLEARED_GENERATION" \
  --file "downloads/cleared-payload/carrier-CLEARED_SHA256.egg.zip"
```

Add `--manifest downloads/cleared-index.json` to **each** command if using a
separately downloaded public release index. Never obtain the index from a
private helper or silently change its expected whole-carrier identity.

The downloader saves `part-0001.bin`, etc., using index-derived local names, not
untrusted remote filenames. Completed parts are SHA-256 checked before reuse.
Incomplete downloads use `.partial` suffixes; rerunning the same command resumes
with a **chunk-local** HTTP Range offset. Content-Range start/end/total and length
must match. A server returning a full `200` response instead of honoring Range
causes a safe restart, never concatenation of a full body onto a prefix.

Each newly completed part is rehashed from disk before atomically becoming a
`.bin` file. A wrong checksum or truncated response fails the command and never
publishes that part as complete. Reassembly rechecks every part in contiguous
whole-carrier offset order and computes the whole SHA-256 while writing. Only a
fully verified result receives the final `.egg.zip` name. A verified existing
archive is an idempotent no-op.

### Recovery and negative controls

- **Interrupted download:** rerun `download-distribution` with the same index and
  directory. Already verified parts are reused; an unfinished prefix resumes.
- **Corrupt completed part or bad unfinished prefix:** rerun
  `download-distribution ... --repair`. This repairs corrupt owned parts and
  discards unfinished prefixes; it does not replace correct completed parts.
- **Interrupted reassembly:** rerun `assemble-distribution`. Assembly restarts
  from verified parts, not from an untrusted partial archive.
- **Corrupt existing assembled archive:** normal assembly refuses to overwrite it.
  `assemble-distribution ... --repair` explicitly discards that corrupt owned
  archive and rebuilds. A correct archive remains untouched.
- **Wrong index/cache pairing:** the cache's `.download-state.json` binds the
  exact descriptor. A changed index requires a new directory; mixing versions
  is refused, even when generation labels match.
- **Wrong offsets, part hash, whole hash, size, response range or path:** fail with
  a bounded error code. No success fallback, extraction or activation.
- **Concurrent command:** `.download.lock` serializes operations in one cache.
  Interruptions that unwind normally remove the lock. After a hard kill or power
  loss, inspect the recorded PID and confirm no transfer still runs before
  manually removing that cache's stale lock. Do not bypass an active lock.

Directories must be below this checkout's ignored `downloads/` directory and
must be empty or already bound to this exact descriptor. Symlinks, nonregular
files, unsafe path components and unrecognized cache entries are refused.
Repair touches only known files inside that bound cache, never arbitrary user
files or remote assets. These locks coordinate this CLI; do not modify cache
files concurrently using other programs.

Defaults: a **32 GiB whole-carrier ceiling**, **6-hour operation budget**, up to
60 seconds per blocking network read, and 1 MiB streaming buffers. Time checks
run between bounded reads; an in-flight read can extend the deadline by at most
its socket timeout. `--max-bytes N` and `--max-seconds N` are explicit overrides
(time ceiling: 24 hours per invocation). No unbounded retry loop is used.
Retries/resumes are separate invocations with fresh operation budgets.

Space checks fail before transfers/assembly when the required additional space
is unavailable. Parts need roughly the future payload's byte length; retaining
them plus its reassembled archive needs roughly twice that length, plus
filesystem overhead. The filesystem must support the resulting individual file
size. External activity can still exhaust a disk after preflight;
errors remain failures and incomplete files are never treated as verified.

`verify-carrier` refuses withheld identities. For an otherwise unblocked future
asset, it can check a user-supplied local file before publication while retaining
the index's publication/screening states. A matching local hash does **not**
create public availability. Download and assembly additionally require a
published, explicitly cleared, descriptor-bound publication receipt.

These commands are intentionally absent from hourly Actions. Metadata polling
does not transfer raw payloads, spend public bandwidth on no-op downloads, or
bypass the publication gate. The withheld original remains locally preserved;
the public deliverable is the safe metadata/query projection.
