PRAGMA page_size = 4096;
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA temp_store = MEMORY;
PRAGMA foreign_keys = ON;
PRAGMA application_id = 1380012112;
PRAGMA user_version = 6;

CREATE TABLE generations (
  generation_id TEXT PRIMARY KEY,
  parent_generation_id TEXT,
  captured_at TEXT NOT NULL,
  inventory_status TEXT NOT NULL CHECK(inventory_status = 'complete_frozen_scope'),
  runtime_compatibility TEXT NOT NULL CHECK(runtime_compatibility = 'not_established')
) WITHOUT ROWID;

CREATE TABLE source_locks (
  generation_id TEXT NOT NULL REFERENCES generations,
  source TEXT NOT NULL,
  identity TEXT NOT NULL,
  PRIMARY KEY (generation_id, source)
) WITHOUT ROWID;

CREATE TABLE repos (
  generation_id TEXT NOT NULL REFERENCES generations,
  unit_id TEXT NOT NULL,
  repository_id INTEGER,
  full_name TEXT NOT NULL,
  public_url TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN ('owner', 'dependency')),
  captured_commit TEXT,
  default_branch TEXT,
  license_spdx TEXT,
  archived INTEGER,
  fork INTEGER,
  empty INTEGER NOT NULL,
  tracked_entries INTEGER NOT NULL,
  rapp_evidence INTEGER NOT NULL,
  compatibility TEXT NOT NULL CHECK(compatibility = 'not_established'),
  entry_binding_sha256 TEXT NOT NULL,
  PRIMARY KEY (generation_id, unit_id)
) WITHOUT ROWID;

CREATE TABLE files (
  generation_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  path TEXT,
  path_bytes_base64 TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('100644', '100755', '120000', '160000')),
  git_object TEXT NOT NULL,
  PRIMARY KEY (generation_id, unit_id, path_bytes_base64),
  FOREIGN KEY (generation_id, unit_id) REFERENCES repos
) WITHOUT ROWID;

CREATE TABLE blobs (
  generation_id TEXT NOT NULL REFERENCES generations,
  git_object TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  bytes_hashed INTEGER NOT NULL CHECK(bytes_hashed = bytes),
  content_status TEXT NOT NULL,
  integrity TEXT NOT NULL CHECK(integrity = 'verified_git_blob'),
  reference_count INTEGER NOT NULL,
  PRIMARY KEY (generation_id, git_object)
) WITHOUT ROWID;

CREATE INDEX files_by_blob ON files(generation_id, git_object);
CREATE INDEX blobs_by_sha256 ON blobs(sha256);
CREATE INDEX repos_by_name ON repos(full_name);

CREATE TABLE dependency_links (
  generation_id TEXT NOT NULL,
  parent_unit_id TEXT NOT NULL,
  path TEXT,
  path_bytes_base64 TEXT NOT NULL,
  "commit" TEXT NOT NULL,
  public_url TEXT,
  status TEXT NOT NULL CHECK(status IN ('hydrated', 'unavailable')),
  dependency_unit_id TEXT,
  PRIMARY KEY (generation_id, parent_unit_id, path_bytes_base64),
  FOREIGN KEY (generation_id, parent_unit_id) REFERENCES repos,
  FOREIGN KEY (generation_id, dependency_unit_id) REFERENCES repos
) WITHOUT ROWID;

CREATE TABLE status_counts (
  generation_id TEXT NOT NULL REFERENCES generations,
  unit_id TEXT,
  category TEXT NOT NULL,
  status TEXT NOT NULL,
  count INTEGER NOT NULL CHECK(count > 0),
  FOREIGN KEY (generation_id, unit_id) REFERENCES repos
);
CREATE UNIQUE INDEX status_identity ON status_counts(generation_id, coalesce(unit_id, ''), category, status);

CREATE TABLE authority_context (
  role TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  "commit" TEXT NOT NULL,
  revision TEXT,
  public_url TEXT,
  pull_request_number INTEGER,
  spec_path TEXT,
  spec_sha256 TEXT
) WITHOUT ROWID;

CREATE TABLE distributions (
  generation_id TEXT PRIMARY KEY REFERENCES generations,
  carrier_bytes INTEGER NOT NULL,
  carrier_sha256 TEXT NOT NULL,
  frame_hash TEXT NOT NULL,
  publication_status TEXT NOT NULL,
  screening_status TEXT NOT NULL,
  clear_for_exact_publication INTEGER NOT NULL,
  verification_json TEXT
) WITHOUT ROWID;

CREATE TABLE release_parts (
  generation_id TEXT NOT NULL REFERENCES distributions,
  part_index INTEGER NOT NULL,
  byte_offset INTEGER NOT NULL,
  bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  public_url TEXT NOT NULL,
  PRIMARY KEY (generation_id, part_index)
) WITHOUT ROWID;

CREATE TABLE observation_events (
  sequence INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  previous_sha256 TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL
);

CREATE TABLE change_observations (
  sequence INTEGER NOT NULL REFERENCES observation_events,
  repository_id INTEGER NOT NULL,
  before_sha256 TEXT,
  after_sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,
  after_json TEXT NOT NULL,
  PRIMARY KEY (sequence, repository_id)
) WITHOUT ROWID;

CREATE TABLE observed_repos (
  repository_id INTEGER PRIMARY KEY,
  full_name TEXT NOT NULL,
  public_url TEXT NOT NULL,
  default_branch TEXT,
  observed_head TEXT,
  availability TEXT NOT NULL,
  previous_names_json TEXT NOT NULL,
  archived INTEGER NOT NULL,
  fork INTEGER NOT NULL,
  license_spdx TEXT,
  last_changed_at TEXT,
  last_event_sequence INTEGER REFERENCES observation_events
);

CREATE TABLE workflow_observations (
  observation_id TEXT PRIMARY KEY,
  observed_date TEXT NOT NULL,
  workflow TEXT NOT NULL,
  result TEXT NOT NULL,
  public_starting_point TEXT NOT NULL,
  source_repository TEXT NOT NULL,
  source_commit TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_bytes INTEGER NOT NULL,
  source_sha256 TEXT NOT NULL,
  reference_repository TEXT NOT NULL,
  reference_commit TEXT NOT NULL,
  reference_rapp_py_sha256 TEXT NOT NULL,
  producer TEXT NOT NULL,
  receiver TEXT NOT NULL,
  source_summary_sha256 TEXT NOT NULL,
  reporting_basis TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE workflow_measurements (
  observation_id TEXT NOT NULL REFERENCES workflow_observations,
  category TEXT NOT NULL CHECK(category IN ('measured', 'scope', 'reference')),
  name TEXT NOT NULL,
  value INTEGER NOT NULL,
  PRIMARY KEY(observation_id, category, name)
) WITHOUT ROWID;

CREATE TABLE workflow_integrations (
  observation_id TEXT PRIMARY KEY REFERENCES workflow_observations,
  repository TEXT NOT NULL,
  tested_source_commit TEXT NOT NULL,
  pull_request_number INTEGER NOT NULL,
  pull_request_url TEXT NOT NULL,
  merge_commit TEXT NOT NULL,
  target_branch TEXT NOT NULL,
  merged_at TEXT NOT NULL,
  state TEXT NOT NULL,
  reference_protocol_ratified INTEGER NOT NULL CHECK(reference_protocol_ratified = 0)
) WITHOUT ROWID;

CREATE VIEW repo_observations AS
SELECT r.generation_id, r.unit_id, r.repository_id, r.full_name,
       r.public_url, r.scope, r.captured_commit, r.license_spdx,
       r.tracked_entries, r.rapp_evidence, r.compatibility,
       coalesce(o.availability, 'not_polled') AS availability,
       CASE WHEN o.availability IN ('public', 'renamed_public') THEN o.observed_head END AS observed_public_head,
       o.observed_head AS last_known_public_head, o.last_changed_at,
       CASE
         WHEN o.availability IS NULL OR o.availability = 'not_polled' THEN 'unknown'
         WHEN o.availability = 'not_listed_public' THEN 'unavailable'
         WHEN o.availability = 'observer_excluded' THEN 'observer_excluded'
         WHEN o.availability = 'empty' AND r.captured_commit IS NULL THEN 'both_empty'
         WHEN o.observed_head = r.captured_commit THEN 'matches_carried_snapshot'
         WHEN o.observed_head IS NOT NULL AND r.captured_commit IS NULL THEN 'uncarried_public_head'
         WHEN o.observed_head IS NOT NULL THEN 'different_from_carried_snapshot'
         ELSE 'unknown'
       END AS head_relation
FROM repos r LEFT JOIN observed_repos o ON o.repository_id = r.repository_id;

CREATE VIEW catalog_summary AS
SELECT g.generation_id, g.parent_generation_id, g.captured_at,
       g.inventory_status, g.runtime_compatibility,
       (SELECT count(*) FROM repos r WHERE r.generation_id=g.generation_id AND r.scope='owner') AS owner_repos,
       (SELECT count(*) FROM repos r WHERE r.generation_id=g.generation_id AND r.scope='dependency') AS dependency_repos,
       (SELECT count(*) FROM files f JOIN repos r USING(generation_id,unit_id)
        WHERE f.generation_id=g.generation_id AND r.scope='owner') AS owner_files,
       (SELECT count(*) FROM files f JOIN repos r USING(generation_id,unit_id)
        WHERE f.generation_id=g.generation_id AND r.scope='dependency') AS dependency_files,
       (SELECT count(*) FROM blobs b WHERE b.generation_id=g.generation_id) AS blobs,
       (SELECT count(*) FROM dependency_links d WHERE d.generation_id=g.generation_id) AS gitlinks,
       (SELECT count(*) FROM dependency_links d WHERE d.generation_id=g.generation_id AND d.status='unavailable') AS unavailable_gitlinks,
       (SELECT coalesce(sum(bytes),0) FROM blobs b WHERE b.generation_id=g.generation_id) AS source_blob_bytes
FROM generations g;

CREATE VIEW metadata_payload_boundaries AS
SELECT g.generation_id,
       d.carrier_sha256 AS projection_of,
       g.inventory_status AS metadata_coverage,
       'allowlisted_metadata_only' AS projection_kind,
       0 AS raw_payload_included,
       d.publication_status AS payload_availability,
       d.clear_for_exact_publication,
       0 AS encrypted_full_upload_allowed
FROM generations g JOIN distributions d USING(generation_id);
