# RAPP public organism catalog

Public, reproducible distribution plumbing for **RAPP**. Git records small,
normalized metadata and meaningful public-source changes; SQLite and a static
catalog are generated outside Git. This is not another product, a hosted database
service, a new RAPP implementation, or a runtime activation mechanism.

**The exact full carrier is WITHHELD; `clear_for_exact_publication: false`.**
The public artifact is an explicitly allowlisted metadata/query **`projection_of`**
`c1a86a55bece80df11cfc5e747cf016c83d4cf4ad5347dab9f7ce89233d0e58d`.
Its complete frozen-scope metadata does not include or make available the
raw/private payload. No raw or encrypted-full upload/download is offered.
Public-safe reviewed code links and sanitized metadata remain available.

- **Source:** <https://github.com/kody-w/rapp-organism>
- **Read and follow public updates:** <https://kody-w.github.io/dogg/> for people
  and AI users; search and SQL here are for inventory/provenance questions.
- **Catalog after a successful Pages deployment:** <https://kody-w.github.io/rapp-organism/>
- **Agent entrypoint:** [llms.txt](llms.txt)
- **Machine entrypoint:** `index.json` at the catalog URL
- **Query:** downloadable `catalog.sqlite`, local CLI, or
  [Datasette Lite](https://lite.datasette.io/?url=https%3A%2F%2Fkody-w.github.io%2Frapp-organism%2Fcatalog.sqlite)

Pages and Actions must be enabled by the publisher. A URL in this README is not
proof that it is deployed. Check its HTTP response, `index.json`, `SHA256SUMS` and
the latest workflow run. There is **no server-side SQL API**: Datasette Lite runs
Python/SQLite in your browser and downloads the database through Pages' CORS support.

DOGG's [public starter roster](https://kody-w.github.io/dogg/subscriptions.json)
is the sole curated following list. The catalog links to that authority; it does
not copy a second roster or import personal following choices. Following remains
read-only observation, not trust or permission to execute record contents.

## Public cold start: only the repository URL

Requirements: Git and Python **3.11 or later**, including its standard `sqlite3`
module. No packages to install, no API credentials, no private capture directory,
no Git LFS, and no release asset needed for metadata queries.

```sh
git clone https://github.com/kody-w/rapp-organism.git
cd rapp-organism
python3 -m unittest discover -s tests -v
python3 -m rapp_catalog validate
python3 -m rapp_catalog build
python3 -m rapp_catalog query "SELECT * FROM catalog_summary"
python3 -m rapp_catalog query "SELECT publication_status, screening_status, carrier_bytes FROM distributions"
python3 -m rapp_catalog query "SELECT * FROM metadata_payload_boundaries"
python3 -m rapp_catalog query "SELECT role, status, \"commit\" FROM authority_context"
```

The initial `g0002` generation must return **489 owner repositories, 163,303 owner
paths, 3 hydrated dependencies containing 2,841 paths, 96,781 distinct blobs,
15 gitlinks and 12 unavailable gitlinks**. Gitlinks are included in the owner
path count but are not blob rows. Seven owner repositories were empty at capture.
The generated products are under `site/`, which is Git-ignored.

An executable cold-start assertion:

```sh
python3 - <<'PY'
import sqlite3
db = sqlite3.connect("file:site/catalog.sqlite?mode=ro", uri=True)
actual = db.execute("""
  SELECT owner_repos, owner_files, dependency_repos, dependency_files,
         blobs, gitlinks, unavailable_gitlinks
  FROM catalog_summary WHERE generation_id='g0002'
""").fetchone()
assert actual == (489, 163303, 3, 2841, 96781, 15, 12), actual
assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
assert db.execute("PRAGMA foreign_key_check").fetchall() == []
db.close()
print("Complete frozen-scope metadata, verified without private inputs.")
PY
```

For a local browser preview, run `python3 -m http.server 8000 --directory site`
and visit `http://localhost:8000/`. Stop the foreground server when finished.
The Datasette links intentionally load the **published Pages database**, not
localhost. Local query examples do not need Datasette or a running server.

### Query examples

```sh
python3 -m rapp_catalog query "SELECT full_name, captured_commit, observed_public_head, availability, head_relation FROM repo_observations WHERE scope='owner' ORDER BY full_name LIMIT 20"
python3 -m rapp_catalog query "SELECT r.full_name, f.path, f.path_bytes_base64, f.mode, f.git_object FROM files f JOIN repos r USING(generation_id,unit_id) WHERE f.path LIKE '%rapp%' LIMIT 20"
python3 -m rapp_catalog query "SELECT sha256, bytes, reference_count FROM blobs ORDER BY reference_count DESC LIMIT 10"
python3 -m rapp_catalog query "SELECT category, status, sum(count) AS occurrences FROM status_counts WHERE category='artifact_declaration' GROUP BY category,status" --format csv
python3 -m rapp_catalog query "SELECT sequence, observed_at, snapshot_sha256 FROM observation_events ORDER BY sequence DESC LIMIT 10"
```

The CLI opens SQLite with `mode=ro&immutable=1`, rejects write/attach/DDL operations,
limits query work and fails explicitly if results exceed `--limit` (default 1,000).
Use SQL filtering or set a larger explicit limit; results are never silently cut.
JSON preserves values; CSV prefixes formula-leading strings with an apostrophe for
spreadsheet safety.

## What is captured, observed, and not established?

| Fact | Representation | What it does **not** establish |
|---|---|---|
| Complete frozen owner scope | `generations`, `repos`, `files`, `blobs` | Today's live source contents |
| Carried source commits | `repos.captured_commit` | Current public HEAD or accepted authority |
| Public API observations | `observed_repos`, append-only events | File recapture or runtime operation |
| Last successful poll | Separate, untracked `freshness.json` | Success of a later failed workflow |
| Parser/declaration measurements | Bounded `status_counts` categories | Runtime compatibility, trust or ratification |
| Accepted canonical-main checkpoint | `authority_context` role `accepted` | An earlier content-source commit or a separate PR |
| Normative rev-15 content | `authority_context` role `content_source`, SPEC SHA-256 | The later accepted canonical-main checkpoint |
| Reviewed proposal | Separate candidate role and commit | Accepted main or activation |
| Known carrier receipt | `data/distribution.json` | Public availability or redistribution clearance |
| Withheld raw/private payload | `data/payload-policy.json`, `metadata_payload_boundaries` | A functioning download or an encryption exception |

Three authority roles are recorded separately for
[kody-w/rapp-1](https://github.com/kody-w/rapp-1):

- **Accepted canonical-main checkpoint:** `eb50008011447f5e69372ac22a1755f0978d15ed`.
- **Rev-15 normative content source:** `58058d08c9f0e340fae07c9865647f599c32634d`.
  Its `SPEC.md` SHA-256 is
  `348e7d5baa94aaf2ce4c5354f3cb261f389298a04af65e271a686d3b62f7c384`.
  The frozen inventory also used this content-source commit as its measuring
  witness; its immutable generation provenance is unchanged.
- **Reviewed implementation candidate:** public [PR #38](https://github.com/kody-w/rapp-1/pull/38)
  at `8a83b3fa8bebe16411fc4a130c6447ace15b43ac`, unmerged when checked.

Neither the earlier content-source commit nor the unmerged candidate replaces
the accepted checkpoint. Passing public protocol, documentation or credential
checks is not merger, ratification or activation. These are publisher-pinned
identities, not a claim that the current branch head has just been rechecked.
Authority JSON schema 2 exposes the explicit third role and SPEC binding.
SQLite `user_version=6` exposes carrier offsets, exact-publication clearance,
metadata/payload boundaries, separately scoped workflow observations and source
integration milestones.

## Reported measured workflows, not universal certification

`data/workflows/` contains allowlisted, hash-bound public-safe summaries of
independent exercises. `workflow_observations` stores the source/runtime
identities; `workflow_measurements` separately stores numeric measurements and
scope flags. These are **reported observations**, not runs performed again by
the catalog, GitHub-head polling receipts, or whole-organism readiness flags.

The 2026-09-06 public DOGG bridge exercise started with only
[the public branch URL](https://github.com/kody-w/dogg/tree/feat/rapp1-bridge-20260906)
at commit `7fce94a13f1537e214c6c612d085ac344679bbd1`: real CPython 3.14.4
produced one verified frame; Node.js 25.2.0 in a separate directory recovered
all 551 bytes of `ticks/854.json`. The report records zero source repairs,
no private bootstrap, 27 bridge contracts passed, 1,652 native frames and seven
chains checked, unchanged-repeat behavior and overwrite refusal.

Scope is **unsigned local snapshot integrity/recovery on one physical
machine**. This does not establish authenticated consumer acceptance,
OS-enforced sandboxing, different-machine operation, recovered-code execution,
full-organism runtime compatibility or a new accepted protocol revision.
The reference implementation remains the unmerged candidate, not the accepted
checkpoint. Historical native records were not rewritten. The full carrier
remains WITHHELD.

**DOGG source integration is a separate fact.** [DOGG PR #4](https://github.com/kody-w/dogg/pull/4)
merged into `main` at `b4c45def3a5951eb15dc5a6eed7a1a7d20be68fe`; stable
[DOGG main](https://github.com/kody-w/dogg/tree/main) now includes the bridge.
The tested source commit `7fce94a13f1537e214c6c612d085ac344679bbd1` remains
retained and is not replaced by the merge commit in the historical observation.
`data/workflow-integrations.json` and `workflow_integrations` record this milestone.
RAPP1 PR #38 at `8a83b3fa8bebe16411fc4a130c6447ace15b43ac` is still the
unmerged reference implementation candidate: a DOGG merge does not ratify RAPP1.

The retained public-summary SHA-256 is
`ffe70bfcf2ed2b3c5bdd58f28ec2be0b88755036ebcb5b8141df3d28e234beff`;
no raw local logs, environment paths or private evidence directories are
copied or needed to build this observation.

```sh
python3 -m rapp_catalog query "SELECT observation_id,result,producer,receiver,source_commit,source_bytes,reporting_basis FROM workflow_observations"
python3 -m rapp_catalog query "SELECT category,name,value FROM workflow_measurements ORDER BY category,name"
python3 -m rapp_catalog query "SELECT tested_source_commit,merge_commit,target_branch,state,reference_protocol_ratified FROM workflow_integrations"
```

All captured repository compatibility values remain `not_established`.
`declared_legacy_rapp:*`, `declared_rapp1:*`, `unverified_history`,
`unverified_trust`, `refused` and grammar-only acceptance retain their separate
meanings. An accepted *artifact measurement* is not accepted *source authority*.

An empty observation ledger means **not polled**, not “everything is current.”
Missing repositories from two complete public traversals become
`not_listed_public`; their last-known heads remain historical evidence, while
`repo_observations.observed_public_head` becomes null. Missing may mean deletion,
privatization, transfer or another availability change—we do not guess which.
Renames within the owner scope are recorded by stable GitHub ID and keep
`renamed_public` plus the previous names. Nothing is deleted from history.

## Public source projection and complete coverage

`data/generations/g0002/` is an immutable, **allowlisted projection**. Its manifest
declares each table's ordered columns, canonical JSONL partitions, row counts,
byte lengths, SHA-256 locks, source identities and scope totals. A JSONL line is
an array in that manifest's column order. Partitions target 4 MiB, far below Git's
50 MiB warning and 100 MiB hard file limit. Initial metadata must stay below
100 MiB; builds fail before publishing if that budget is exceeded.

The projection includes:

- GitHub repository IDs/names/public URLs, captured commits, license detection,
  empty/fork/archive flags, measured counts and bounded evidence categories;
- **every** owner and hydrated-dependency path, lossless raw-path base64,
  UTF-8 path where valid, Git mode and object ID;
- every referenced unique blob's Git ID, SHA-256, size, hashed byte count,
  integrity/content category and reference count;
- hydrated and unavailable gitlink bindings, generation parent identity, source
  locks, aggregate artifact/status outcomes, and semantic public-source history.

It excludes source bodies, raw analysis/evidence, code snippets, descriptions,
arbitrary API fields, full diagnostics, credential material and local locations.
Paths are data, never extraction targets. Non-UTF-8 filenames retain raw base64;
their display path is null. Unsafe absolute/traversal paths and credential/local
location patterns fail the **entire import**, rather than dropping rows.
Legitimate relative paths such as `src/users/example.py` remain lossless.

The importer cross-checks the scope, repository index, dependency index and
corrected matrix: exact repository sets, commits, URLs, flags, entry counts and
modes, blob closure/reference counts, source byte totals and all gitlinks. It
does not open raw blob bodies or repeat their prior content-hashing analysis.
Content hashes are claims of the pinned source matrix, bound to its exact bytes.
The initial source matrix SHA-256 is
`036237ffc6fe6527f466a3fe8407adcec70f3214a6caee5eb4aee07ca7ad4ddd`.
Original source hashes (not source paths) are recorded in the generation manifest.

**New clones build from the public JSONL alone.** The one-time importer is for
publishers with already reviewed capture inputs:

```sh
python3 -m rapp_catalog import-seed \
  --scope capture/source-scope.json \
  --genome capture/genome-index.json \
  --dependencies capture/dependency-index.json \
  --matrix capture/estate-matrix-corrected-001.json \
  --receipt capture/full-carrier.receipt.json \
  --generation g0002 --parent g0001 \
  --expect-matrix-sha256 036237ffc6fe6527f466a3fe8407adcec70f3214a6caee5eb4aee07ca7ad4ddd
```

Input locations are CLI parameters, never public metadata. Existing generations
can be re-imported only if their bytes are identical. A real update needs a new
generation ID and reviewed scope. The initial parent `g0001` is a provenance
reference, not an assertion that its own metadata is indexed here.
The RAPP1 proposal is separately identified by authority/distribution records;
it is not silently counted as additional files in the frozen 489-repository scope.
`data/observations/baseline.json` pins the immutable observation starting point;
adding a generation does not reinterpret old events.

## Git scraping: changes, not polling noise

Inspired by [Simon Willison's git-scraping article](https://simonwillison.net/2020/Oct/9/git-scraping/)
and [ca-fires-history](https://github.com/simonw/ca-fires-history).

The scraper uses only `api.github.com/users/{owner}` and the owner's **public**
repository listing, followed by public branch-reference endpoints. It never calls
a private-inclusive authenticated repository listing. Private or inconsistent
rows cause failure. It never reads files or commits' raw source bodies.

1. Obtain the public repository count and traverse every page in deterministic
   order. Verify page lengths, pagination, IDs, owner, public visibility and total.
2. Resolve heads when normalized `pushed_at`/default-branch/name metadata changed,
   a cached head expired (24 hours), or `--refresh-heads` was requested. Use ETags
   and revalidate cached response shapes.
3. Repeat the **complete metadata traversal**. Any mismatch, rate limit, redirect,
   HTTP error, invalid JSON, truncation or unstable scope fails the run.
4. Only a completely validated candidate can append one atomic event under
   `data/observations/`. It contains changed records, before hashes, time,
   sequence and chained snapshot hashes. Identical inputs create no event.
5. Rebuild and validate before attempting a change-only commit. Commit and push
   errors fail the workflow; a clean no-op is explicitly distinguished from error.

Two matching traversals are a consistency fence, not a transactional GitHub
snapshot: a repository can change immediately after observation. A fresh receipt
means the public metadata traversal succeeded; cached head probes may be up to
24 hours old. New source bodies are not fetched. API request budgets and timeouts
fail explicitly rather than silently sampling part of the organism.

The observer repository's own generated head, `size` and `pushed_at` are excluded
from observation. Its publication commits cannot recursively generate further
head-change commits. Poll time, cache validation time and ETags live only in
`.cache/`, never the semantic Git source. There are **no keepalive/no-op commits**.

Historical query products are reproducible by replaying the explicit, append-only
event ledger. Every event is hash-checked against the pinned baseline and previous
state; gaps, edits, nonsemantic records and deletions fail validation.

### Automation and freshness

The pinned-action workflow polls at minute **17** each hour. Standard public
Actions runners need no paid warehouse. Only the scraper job has `contents: write`.
The deployment job alone has `pages: write` and `id-token: write`. PR checks have
read-only contents, no persisted checkout credentials, and do not scrape/deploy.
Concurrency serializes main's snapshot writers; runs have finite timeouts.

Scheduled jobs may be late, dropped, or disabled after inactivity. The UI computes
staleness from a separate published poll receipt (6-hour threshold); build-only
deployments honestly show unknown freshness. The latest failed attempt is visible
in Actions; last-good Pages remains intact and its successful receipt ages normally.
No per-hour timestamp is added to the database or committed metadata.

Publishers can manually run **RAPP public catalog → Run workflow → mode: poll**.
Use `mode: build` to rebuild without polling. Local polling is optional:

```sh
python3 -m rapp_catalog scrape
python3 -m rapp_catalog build --freshness .cache/last-run.json
```

First-time polling hundreds of heads can exceed GitHub's anonymous API quota.
Actions supplies its ephemeral `GITHUB_TOKEN`; no private key or user secret is
needed. Local users may supply an existing `GITHUB_TOKEN`/`GH_TOKEN`, never saved
to data or logs. HTTP caches contain only projected public metadata. A local
concurrent scraper fails with `scraper_already_running`; after a hard crash, verify
no local scrape process is active before removing the stale `.cache/scrape.lock`.

## Generated query products and reproducibility

`python3 -m rapp_catalog build` verifies all text inputs, constructs SQLite in a
repository-local staging directory, runs integrity/foreign-key checks, queries
real row counts, executes every query preset and only then replaces the output.
SQLite contains normalized generations, source locks, repos, files, blobs,
gitlinks, status counts, authority context, distributions and observation history.
`schema.sql` documents columns and convenience views.

Pages includes the database, small repository JSON/CSV products, complete
immutable JSONL partitions, explicit manifests and `SHA256SUMS`. No generated DB
is committed. The build enforces a conservative 750 MiB Pages budget, below the
1 GiB artifact ceiling. The small catalog page does not download SQLite until
you ask it to; queries over the full DB may need substantial browser memory.
The initial measured footprint is about **51.6 MB of text metadata, 100.6 MB of
SQLite, and 153 MB for the complete Pages output** (decimal MB). Exact current
sizes and hashes are emitted by the build and machine index.

The DB has deterministic schema, insert order, page settings and no build-clock
fields. Rebuilds are byte-identical with the same Python/SQLite implementation.
Across SQLite versions, the portable guarantee is canonical source bytes,
schema/row contents and verified counts—not an unsupported promise about SQLite's
binary format. `artifact-manifest.json` records the SQLite version and all output
hashes. A supplied freshness receipt changes only the separate freshness/artifact
receipts, not the DB.

```sh
python3 -m rapp_catalog build --output site
python3 -m rapp_catalog build --output site-check
python3 - <<'PY'
from pathlib import Path
import hashlib
paths = [Path("site/catalog.sqlite"), Path("site-check/catalog.sqlite")]
hashes = []
for path in paths:
    with path.open("rb") as stream:
        hashes.append(hashlib.file_digest(stream, "sha256").hexdigest())
assert hashes[0] == hashes[1], hashes
print(hashes[0])
PY
```

## Exact full carrier: WITHHELD

The known `g0002` full transport is **21,919,507,382 bytes**, SHA-256
`c1a86a55bece80df11cfc5e747cf016c83d4cf4ad5347dab9f7ce89233d0e58d`,
frame hash
`af2446a35c15e1d14670057c4fd9f80c78c7668803857f2322b49df4a760cd11`.
It preserves the prior organism plus a reviewed source proposal, not ratified
source or an activated runtime.

**Definitive state: `withheld`, screening `blocked`,
`clear_for_exact_publication: false`, no URLs and no public parts.** The exact
payload did not clear disclosure and rights review. Detailed findings, sensitive
locators and raw diagnostics are not part of this public projection.

The complete original is retained locally by its custodian; this catalog does
not move, alter, delete or upload it. Encryption is not an allowed publication
workaround. Keep the carrier, raw matrix, Git bundles and generated SQLite out
of Git. A matching hash or complete metadata inventory is not payload clearance.

`data/payload-policy.json` records the minimal public-safe withholding decision.
All four carrier-transfer/verification commands refuse withheld identities with
`carrier_withheld`. An external index cannot override this repository policy,
relabel the blocked generation, or republish the same hash under another ID.

The retained tooling is for **different, separately cleared future assets only**.
[DISTRIBUTION.md](DISTRIBUTION.md) defines distribution schema 3, explicit offsets,
clearance requirements and generic recovery commands. It does not offer a
download path for this withheld carrier. There are no invented chunk URLs.

GitHub Pages has a 1 GiB artifact ceiling, a soft 100 GiB/month bandwidth limit
and build-time limits. Releases have per-asset limits; documented lack of an
aggregate release/bandwidth cap is **not** an unlimited managed-warehouse service
or exemption from GitHub policies. Review current platform documentation and
operational constraints before scaling bulk distribution.

## Publisher checklist

- Review the allowlisted public projection, source locks and [license boundaries](DATA_LICENSES.md).
- Run tests, full validate/build and the cold-start assertions above.
- Commit only source, docs, workflows and `data/` text. Never force-add ignored
  carrier, matrix, archive, SQLite, cache or site output.
- Push reviewed main; enable Actions and Pages with **GitHub Actions** as the build
  source. No remote actions are performed by the importer/build tool.
- Wait for the pinned build/deploy workflow, then verify HTTP 200, CORS, hashes,
  database queries and the actual Datasette Lite link on the public Pages URL.
- Manually dispatch `poll` and check its public receipt, semantic event diff and
  no-op behavior; schedules alone are not a freshness guarantee.
- Publish/test the allowlisted metadata projection and reviewed public-safe code
  only. Do not publish the withheld carrier, including in encrypted-full form.
  Separately cleared future assets require their own identities and the
  documented policy checks; they must not override the known withholding decision.
