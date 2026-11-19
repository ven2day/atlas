-- 0005: the RSS event archive's provenance: capture kind, content revisions, coverage gaps.
--
-- Additive only: one new column with a default (existing rows keep 'unknown') and two new
-- tables. No table or column created by 0001-0004 is altered or dropped, so a database written
-- by earlier code opens unchanged. These are NOT projections: rebuild_projections() must leave
-- them alone, as with every other research_* table.
--
-- Why these three things:
--
-- * ``capture_kind`` separates records this installation genuinely received as they happened
--   ('forward_captured', synced from the event store's AnnouncementReceived events with their
--   real receipt times) from backfills and unknown-provenance imports. ACTUAL-mode evaluation
--   only ever reads forward_captured rows; nothing else may masquerade as a live observation.
-- * ``research_record_revisions`` keeps the history of a record whose content changed under a
--   stable id: the first receipt stays authoritative in research_source_records and every later
--   variant is appended here. Nothing is silently overwritten.
-- * ``research_coverage_gaps`` persists the collector's own gap reports (feed outage, window
--   exceeded) so "we did not look" is never indistinguishable from "nothing happened".

ALTER TABLE research_source_records
    ADD COLUMN capture_kind TEXT NOT NULL DEFAULT 'unknown';

CREATE TABLE research_record_revisions (
    revision_id     TEXT    NOT NULL PRIMARY KEY,
    record_id       TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,
    raw             TEXT    NOT NULL,
    import_batch    TEXT    NOT NULL,
    observed_at_utc TEXT    NOT NULL
);
CREATE INDEX ix_rrev_record ON research_record_revisions (record_id, observed_at_utc);

CREATE TABLE research_coverage_gaps (
    gap_id          TEXT    NOT NULL PRIMARY KEY,
    source          TEXT    NOT NULL,
    gap_from        TEXT    NOT NULL,
    gap_to          TEXT    NOT NULL,
    reason          TEXT    NOT NULL,
    recorded_at_utc TEXT    NOT NULL
);
CREATE INDEX ix_rcg_source ON research_coverage_gaps (source, gap_from);
