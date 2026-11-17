-- 0003: the research layer's own durable state. Additive only - no existing table or column is
-- touched, so a database written by earlier code opens unchanged and simply has empty research
-- tables until scripts/run_research.py runs.
--
-- These are NOT projections of the event log: rebuild_projections() must leave them alone, the
-- same rule that applies to kv_state (0002). They record what the research path *published*, point
-- in time, so a forecast can later be scored against the outcome it did not know.
--
-- Probabilities, log returns and volatilities are REAL: they are statistics, not money. No price
-- or rupee amount is stored here, so the TEXT-decimal convention of 0001 does not apply.
-- Timestamps are ISO-8601 UTC text, as everywhere else.

CREATE TABLE research_forecasts (
    forecast_id      TEXT    NOT NULL PRIMARY KEY,
    instrument_key   TEXT    NOT NULL,
    horizon_sessions INTEGER NOT NULL,
    status           TEXT    NOT NULL,
    baseline         TEXT    NOT NULL,
    as_of_utc        TEXT    NOT NULL,
    bar_date         TEXT,
    regime           TEXT,
    p_up             REAL,
    p_side           REAL,
    p_down           REAL,
    q10              REAL,
    q50              REAL,
    q90              REAL,
    p_underperform   REAL,
    sideways_band    REAL,
    n_samples        INTEGER NOT NULL DEFAULT 0,
    n_unconditional  INTEGER NOT NULL DEFAULT 0,
    trend            TEXT,
    vol_band         TEXT,
    annualized_vol   REAL,
    sigma_daily      REAL,
    data_quality     TEXT    NOT NULL DEFAULT '',
    notes            TEXT    NOT NULL DEFAULT '[]',
    -- Filled in later by the evaluation path; NULL until the horizon has actually elapsed.
    outcome_return            REAL,
    outcome_benchmark_return  REAL,
    outcome_bucket            TEXT,
    outcome_settled_at        TEXT
);
CREATE INDEX ix_research_forecasts_instrument ON research_forecasts (instrument_key, horizon_sessions);
CREATE INDEX ix_research_forecasts_as_of ON research_forecasts (as_of_utc);
CREATE INDEX ix_research_forecasts_unsettled ON research_forecasts (horizon_sessions, as_of_utc)
    WHERE outcome_settled_at IS NULL;

CREATE TABLE research_cards (
    instrument_key   TEXT    NOT NULL,
    horizon_sessions INTEGER NOT NULL,
    as_of_utc        TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    asset            TEXT    NOT NULL,
    symbol           TEXT    NOT NULL DEFAULT '',
    sector           TEXT,
    confirmation     TEXT    NOT NULL,
    confidence       REAL,
    payload          TEXT    NOT NULL,
    PRIMARY KEY (instrument_key, horizon_sessions, as_of_utc)
);
CREATE INDEX ix_research_cards_status ON research_cards (status, as_of_utc);
CREATE INDEX ix_research_cards_sector ON research_cards (sector, as_of_utc) WHERE sector IS NOT NULL;

CREATE TABLE research_event_impacts (
    event_id       TEXT    NOT NULL,
    instrument_key TEXT    NOT NULL DEFAULT '',
    asset          TEXT    NOT NULL,
    scope          TEXT    NOT NULL,
    channel        TEXT    NOT NULL,
    direction      TEXT    NOT NULL,
    materiality    TEXT,
    confidence     REAL,
    basis          TEXT    NOT NULL,
    uncertain      INTEGER NOT NULL DEFAULT 0,
    reason         TEXT    NOT NULL DEFAULT '',
    evidence       TEXT    NOT NULL DEFAULT '[]',
    known_at_utc   TEXT    NOT NULL,
    PRIMARY KEY (event_id, scope, instrument_key)
);
CREATE INDEX ix_research_impacts_instrument ON research_event_impacts (instrument_key, known_at_utc);
