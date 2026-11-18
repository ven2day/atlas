-- 0004: historical event import, provenance, outcomes and walk-forward evaluation.
--
-- Additive only: no table or column created by 0001-0003 is altered, so a database written by
-- earlier code opens unchanged and simply has empty history tables. Existing research
-- publications (0003) and all trading data are preserved as-is.
--
-- These are NOT projections: rebuild_projections() must leave them alone, as with kv_state and
-- the 0003 research tables.
--
-- Design rule enforced by the schema: the four timestamps that decide what was knowable when are
-- separate, non-overwriting columns. Nothing here may be backdated to make a late import look
-- like an early receipt - ``received_at_utc`` is when the source says it published/received, and
-- ``imported_at_utc`` is when this process actually read it, and the two are never conflated.

-- The verbatim source record. Kept whole so any later re-classification or re-mapping can be
-- audited against what the source actually said, rather than against our summary of it.
CREATE TABLE research_source_records (
    record_id        TEXT    NOT NULL PRIMARY KEY,
    source           TEXT    NOT NULL,
    source_id        TEXT,
    symbol           TEXT    NOT NULL,
    event_type       TEXT,
    published_at_utc TEXT    NOT NULL,
    received_at_utc  TEXT,
    document_url     TEXT,
    raw              TEXT    NOT NULL,
    content_hash     TEXT    NOT NULL,
    import_batch     TEXT    NOT NULL,
    imported_at_utc  TEXT    NOT NULL
);
CREATE INDEX ix_rsr_symbol_published ON research_source_records (symbol, published_at_utc);
CREATE INDEX ix_rsr_batch ON research_source_records (import_batch);
CREATE INDEX ix_rsr_source_id ON research_source_records (source, source_id)
    WHERE source_id IS NOT NULL;

-- Import runs and their resume cursors. A partial import is recorded as partial, never as
-- "there were no events in this window".
CREATE TABLE research_imports (
    import_id       TEXT    NOT NULL PRIMARY KEY,
    source          TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    started_at_utc  TEXT    NOT NULL,
    finished_at_utc TEXT,
    range_from      TEXT,
    range_to        TEXT,
    symbols         TEXT    NOT NULL DEFAULT '[]',
    cursor          TEXT,
    fetched         INTEGER NOT NULL DEFAULT 0,
    inserted        INTEGER NOT NULL DEFAULT 0,
    duplicates      INTEGER NOT NULL DEFAULT 0,
    failures        INTEGER NOT NULL DEFAULT 0,
    coverage        TEXT    NOT NULL DEFAULT '[]',
    notes           TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX ix_rimports_source ON research_imports (source, started_at_utc);

-- Classifications, versioned and never overwritten: a correction is a new row pointing at the
-- one it supersedes. ``retrospective`` marks a classification applied to an announcement that
-- predates the model/rule version doing the reading.
CREATE TABLE research_classifications (
    classification_id TEXT    NOT NULL PRIMARY KEY,
    record_id         TEXT    NOT NULL,
    classified_at_utc TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    prompt_version    TEXT,
    rules_version     TEXT,
    retrospective     INTEGER NOT NULL DEFAULT 0,
    event_type        TEXT,
    direction         TEXT,
    materiality       TEXT,
    confidence        REAL,
    supersedes        TEXT,
    payload           TEXT    NOT NULL
);
CREATE INDEX ix_rclass_record ON research_classifications (record_id, classified_at_utc);

-- Provenance of one price pull, so an outcome can always be traced to the data that produced it.
CREATE TABLE research_price_datasets (
    dataset_id         TEXT    NOT NULL PRIMARY KEY,
    instrument_key     TEXT    NOT NULL,
    provider           TEXT    NOT NULL,
    retrieved_at_utc   TEXT    NOT NULL,
    requested_from     TEXT,
    requested_to       TEXT,
    available_from     TEXT,
    available_to       TEXT,
    sessions           INTEGER NOT NULL DEFAULT 0,
    missing_sessions   TEXT    NOT NULL DEFAULT '[]',
    adjustment         TEXT    NOT NULL DEFAULT '',
    benchmark_key      TEXT,
    benchmark_sessions INTEGER,
    notes              TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX ix_rpd_instrument ON research_price_datasets (instrument_key, retrieved_at_utc);

-- One row per (event, horizon). An incomplete horizon stays ``pending`` and is never filled in.
CREATE TABLE research_event_outcomes (
    outcome_id        TEXT    NOT NULL PRIMARY KEY,
    record_id         TEXT    NOT NULL,
    instrument_key    TEXT    NOT NULL,
    horizon_sessions  INTEGER NOT NULL,
    evaluation_mode   TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    anchor_date       TEXT,
    outcome_date      TEXT,
    instrument_return REAL,
    benchmark_return  REAL,
    abnormal_return   REAL,
    bucket            TEXT,
    computed_at_utc   TEXT    NOT NULL,
    notes             TEXT    NOT NULL DEFAULT '[]'
);
CREATE INDEX ix_reo_record ON research_event_outcomes (record_id, horizon_sessions);
CREATE INDEX ix_reo_pending ON research_event_outcomes (status, horizon_sessions)
    WHERE status = 'pending';

-- Frozen walk-forward forecast publications. Immutable: a publication is written once at its
-- origin and only ever gains an outcome afterwards, never a revised probability.
CREATE TABLE research_walkforward (
    publication_id    TEXT    NOT NULL PRIMARY KEY,
    run_id            TEXT    NOT NULL,
    fold              INTEGER NOT NULL,
    origin_date       TEXT    NOT NULL,
    record_id         TEXT    NOT NULL,
    instrument_key    TEXT    NOT NULL,
    horizon_sessions  INTEGER NOT NULL,
    approach          TEXT    NOT NULL,
    cohort            TEXT    NOT NULL,
    p_up              REAL,
    p_side            REAL,
    p_down            REAL,
    q10               REAL,
    q50               REAL,
    q90               REAL,
    n_train           INTEGER NOT NULL DEFAULT 0,
    fallback_used     INTEGER NOT NULL DEFAULT 0,
    configuration     TEXT    NOT NULL DEFAULT '{}',
    published_at_utc  TEXT    NOT NULL,
    outcome_return    REAL,
    benchmark_return  REAL,
    abnormal_return   REAL,
    outcome_bucket    TEXT,
    settled_at_utc    TEXT
);
CREATE INDEX ix_rwf_run ON research_walkforward (run_id, approach, horizon_sessions);
CREATE INDEX ix_rwf_unsettled ON research_walkforward (horizon_sessions, origin_date)
    WHERE settled_at_utc IS NULL;

-- One row per evaluation run: the frozen configuration and the full metrics payload.
CREATE TABLE research_evaluations (
    evaluation_id    TEXT    NOT NULL PRIMARY KEY,
    run_id           TEXT    NOT NULL,
    created_at_utc   TEXT    NOT NULL,
    evaluation_mode  TEXT    NOT NULL,
    horizons         TEXT    NOT NULL DEFAULT '[]',
    configuration    TEXT    NOT NULL DEFAULT '{}',
    payload          TEXT    NOT NULL
);
CREATE INDEX ix_revals_run ON research_evaluations (run_id, created_at_utc);
