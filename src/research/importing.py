"""Repeatable historical corporate-announcement import.

**What is available, stated plainly.** NSE publishes no official free REST endpoint that answers
"give me every corporate announcement between two dates". ``nsearchives.nseindia.com`` serves
individual documents at direct file URLs; the corporate-filings page on ``nseindia.com`` is driven
by internal endpoints that are not documented or offered for programmatic use; date-range history
is sold as NSE's paid *Corporate Data* product or resold by authorized vendors. The live RSS feed
this repository already reads (OD-10) holds only the newest ~165 items, so it is **not** a
historical source and is never used as one here.

Accordingly this module ships two adapters and no invented endpoints:

* :class:`LocalFileSource` - the working, authorized path. You supply an export you are licensed
  to hold (an NSE Corporate Data delivery, an authorized vendor's dump, or your own accumulated
  archive) as JSON or CSV in the schema documented below.
* :class:`HttpArchiveSource` - a *generic* HTTP adapter for an authorized archive you configure
  explicitly, with a field map. It refuses to run without a URL: it will not guess an NSE endpoint,
  and it does not scrape ``nseindia.com``.

The rules that make an import trustworthy:

* **A failed fetch is never an empty result.** A window whose page could not be retrieved is
  recorded as ``failed`` coverage and the import finishes ``partial``. "No events" is only ever
  claimed for a window the source actually answered.
* **Timestamps are not backdated.** ``published_at`` comes from the record; ``imported_at`` is the
  wall clock of this process. Importing a 2024 announcement in 2026 stores 2026 as the receipt.
* **Identity is stable.** ``record_id`` is a hash of the source, its own identifier when it has
  one, and otherwise the symbol, publication time and text - so re-importing the same file twice
  is a no-op rather than a duplicate.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx2

from src.utils.market_time import IST

logger = logging.getLogger(__name__)

# Bounded retry policy: a transient failure is retried, then recorded as a coverage failure.
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_S = 1.5
DEFAULT_MIN_INTERVAL_S = 1.0
DEFAULT_PAGE_LIMIT = 500

# The local file schema. CSV headers, or JSON object keys, must use these names; ``published_at``
# is ISO-8601 and is read as IST when it carries no offset (NSE publishes IST).
LOCAL_SCHEMA_FIELDS = (
    "symbol",
    "event_type",
    "published_at",
    "source_id",
    "document_url",
    "subject",
    "title",
    "body",
    # Optional, and only honoured when the licensed source actually supplies them. A direction or
    # materiality read from the source is recorded as source-provided; otherwise the rule table
    # decides the direction and materiality stays "unrated" rather than being guessed.
    "direction",
    "materiality",
)
LOCAL_SOURCE_NAME = "local_file"


class ImportSourceError(RuntimeError):
    """The source could not answer. Retryable if ``retryable``."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class SourceNotConfiguredError(RuntimeError):
    """An adapter was asked to run without the configuration that authorizes it."""


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One announcement as the source gave it, plus the identity we will deduplicate on.

    ``received_at`` is the receipt time the *source itself recorded* (the live RSS collector's
    own timestamp, synced from the event store). When it is None the store falls back to the
    import time - which for a backfill is honestly "we do not know when this was first seen".
    ``capture_kind`` is ``forward_captured`` only for records whose receipt time is a real
    recorded observation; ACTUAL-mode evaluation reads nothing else.
    """

    source: str
    symbol: str
    published_at: datetime
    raw: Mapping[str, Any]
    source_id: str | None = None
    event_type: str | None = None
    document_url: str | None = None
    received_at: datetime | None = None
    capture_kind: str = "unknown"

    @property
    def record_id(self) -> str:
        """Stable across re-imports: the source's own id when it has one, else a content hash."""
        if self.source_id:
            basis = f"{self.source}|id|{self.source_id}"
        else:
            basis = (
                f"{self.source}|content|{self.symbol}|"
                f"{self.published_at.astimezone(UTC).isoformat()}|{self.text()[:400]}"
            )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]

    @property
    def content_hash(self) -> str:
        """A hash of the verbatim record, so a changed body under the same id is detectable."""
        return hashlib.sha256(
            json.dumps(self.raw, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:32]

    def text(self) -> str:
        for key in ("body", "title", "subject", "text", "description"):
            value = self.raw.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        return ""


@dataclass(frozen=True, slots=True)
class CoverageWindow:
    """What one requested window actually yielded. ``status`` is never guessed."""

    range_from: date
    range_to: date
    status: str  # complete | incomplete | failed
    records: int
    note: str = ""


@dataclass(frozen=True, slots=True)
class SourcePage:
    records: tuple[SourceRecord, ...]
    next_cursor: str | None = None
    # Did the source affirmatively answer for this window? A transport failure is not a "no".
    answered: bool = True
    note: str = ""


class HistoricalSource(Protocol):
    """An adapter for one authorized historical archive."""

    @property
    def name(self) -> str: ...

    @property
    def supports_pagination(self) -> bool: ...

    async def fetch(
        self,
        *,
        range_from: date,
        range_to: date,
        symbols: Sequence[str],
        cursor: str | None,
        limit: int,
    ) -> SourcePage: ...

    async def aclose(self) -> None: ...


def _parse_published(value: Any) -> datetime:
    """ISO-8601, or the exchange's own ``02-Oct-2026 00:55:31``. Naive values are read as IST."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    text = str(value).strip()
    if not text:
        raise ValueError("a record without published_at cannot be placed in time")
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"unrecognised published_at {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


def _text_or_none(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def record_from_mapping(source: str, row: Mapping[str, Any]) -> SourceRecord:
    """One record from the documented schema. Raises ``ValueError`` naming what is unusable."""
    symbol = _text_or_none(row, "symbol")
    if symbol is None:
        raise ValueError("record has no symbol")
    return SourceRecord(
        source=source,
        symbol=symbol.upper(),
        published_at=_parse_published(row.get("published_at")),
        raw=dict(row),
        source_id=_text_or_none(row, "source_id"),
        event_type=_text_or_none(row, "event_type"),
        document_url=_text_or_none(row, "document_url"),
    )


class LocalFileSource:
    """JSON or CSV in the documented schema. No network, no scraping, fully reproducible.

    JSON is either a bare list of records or ``{"records": [...], "source": "..."}``. CSV must
    have a header row using :data:`LOCAL_SCHEMA_FIELDS` names. A file that cannot be parsed raises
    rather than yielding zero records, because "unreadable" must never become "no events".
    """

    def __init__(self, path: Path, *, name: str = LOCAL_SOURCE_NAME) -> None:
        self._path = path
        self._name = name
        self._rows: list[Mapping[str, Any]] | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def supports_pagination(self) -> bool:
        return False  # the whole file is read once; paging it would only hide records

    def _load(self) -> list[Mapping[str, Any]]:
        if self._rows is not None:
            return self._rows
        try:
            body = self._path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ImportSourceError(
                f"cannot read {self._path}: {exc.strerror or exc}", retryable=False
            ) from exc
        rows: list[Mapping[str, Any]]
        if self._path.suffix.lower() == ".csv":
            reader = csv.DictReader(io.StringIO(body))
            missing = [f for f in ("symbol", "published_at") if f not in (reader.fieldnames or [])]
            if missing:
                raise ImportSourceError(
                    f"{self._path.name} is missing required column(s) {missing}; the schema is "
                    f"{', '.join(LOCAL_SCHEMA_FIELDS)}",
                    retryable=False,
                )
            rows = [dict(r) for r in reader]
        else:
            try:
                parsed = json.loads(body)
            except ValueError as exc:
                raise ImportSourceError(
                    f"{self._path.name} is not valid JSON: {exc}", retryable=False
                ) from exc
            if isinstance(parsed, dict):
                inner = parsed.get("records")
                if not isinstance(inner, list):
                    raise ImportSourceError(
                        f"{self._path.name} has no 'records' list", retryable=False
                    )
                rows = [r for r in inner if isinstance(r, dict)]
            elif isinstance(parsed, list):
                rows = [r for r in parsed if isinstance(r, dict)]
            else:
                raise ImportSourceError(
                    f"{self._path.name} is neither a list nor an object", retryable=False
                )
        self._rows = rows
        return rows

    async def fetch(
        self,
        *,
        range_from: date,
        range_to: date,
        symbols: Sequence[str],
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        wanted = {s.upper() for s in symbols}
        kept: list[SourceRecord] = []
        for row in self._load():
            try:
                record = record_from_mapping(self._name, row)
            except ValueError as exc:
                logger.warning("skipping an unusable record in %s: %s", self._path.name, exc)
                continue
            day = record.published_at.astimezone(IST).date()
            if not (range_from <= day <= range_to):
                continue
            if wanted and record.symbol not in wanted:
                continue
            kept.append(record)
        kept.sort(key=lambda r: (r.published_at, r.record_id))
        return SourcePage(records=tuple(kept[:limit]), next_cursor=None, answered=True)

    async def aclose(self) -> None:
        return None


class HttpArchiveSource:
    """A generic adapter for an archive you are authorized to query and have configured yourself.

    Nothing about NSE is assumed or hard-coded. Without ``url`` this adapter refuses to run: an
    unconfigured archive is a configuration error, not an empty history.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        records_path: str = "records",
        next_cursor_path: str | None = None,
        field_map: Mapping[str, str] | None = None,
        source_name: str = "http_archive",
        timeout_s: float = 30.0,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._params = dict(params or {})
        self._records_path = records_path
        self._next_cursor_path = next_cursor_path
        self._field_map = dict(field_map or {})
        self._name = source_name
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout_s

    @property
    def name(self) -> str:
        return self._name

    @property
    def supports_pagination(self) -> bool:
        return self._next_cursor_path is not None

    def _resolve(self, body: Any, path: str) -> Any:
        node = body
        for part in path.split("."):
            if part == "":
                continue
            if isinstance(node, Mapping):
                node = node.get(part)
            elif isinstance(node, Sequence) and not isinstance(node, str):
                node = node[int(part)]
            else:
                return None
        return node

    async def fetch(
        self,
        *,
        range_from: date,
        range_to: date,
        symbols: Sequence[str],
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        if not self._url:
            raise SourceNotConfiguredError(
                "HttpArchiveSource has no url. This repository ships no authorized historical "
                "announcement endpoint: configure one you are licensed to query, or use "
                "LocalFileSource with an export you hold."
            )
        substitutions = {
            "from": range_from.isoformat(),
            "to": range_to.isoformat(),
            "symbol": symbols[0] if symbols else "",
            "cursor": cursor or "",
            "limit": str(limit),
        }
        params = {k: v.format(**substitutions) for k, v in self._params.items()}
        client = self._client
        if client is None:
            client = httpx2.AsyncClient(timeout=self._timeout, follow_redirects=True)
        try:
            response = await client.get(self._url, params=params, headers=self._headers)
        except httpx2.HTTPError as exc:
            raise ImportSourceError(
                f"{type(exc).__name__} fetching the archive", retryable=True
            ) from exc
        finally:
            if self._client is None and client is not None:
                await client.aclose()
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            raise ImportSourceError(f"archive returned HTTP {response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise ImportSourceError(
                f"archive returned HTTP {response.status_code}", retryable=False
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ImportSourceError("archive response is not JSON", retryable=False) from exc
        raw_records = self._resolve(body, self._records_path)
        if raw_records is None:
            raise ImportSourceError(
                f"archive response has no '{self._records_path}' element", retryable=False
            )
        if not isinstance(raw_records, list):
            raise ImportSourceError(
                f"archive '{self._records_path}' is not a list", retryable=False
            )
        records: list[SourceRecord] = []
        for item in raw_records:
            if not isinstance(item, Mapping):
                continue
            row = {ours: item.get(theirs) for ours, theirs in self._field_map.items()} or dict(item)
            try:
                records.append(record_from_mapping(self._name, row))
            except ValueError as exc:
                logger.warning("skipping an unusable archive record: %s", exc)
        next_cursor = None
        if self._next_cursor_path:
            value = self._resolve(body, self._next_cursor_path)
            next_cursor = str(value) if value not in (None, "") else None
        return SourcePage(records=tuple(records), next_cursor=next_cursor, answered=True)

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


@dataclass
class ImportReport:
    """What an import did, and what it could not establish."""

    import_id: str
    source: str
    status: str  # completed | partial | failed
    range_from: date
    range_to: date
    symbols: tuple[str, ...]
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    rejected: int = 0
    failures: int = 0
    coverage: list[CoverageWindow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cursor: str | None = None

    @property
    def incomplete(self) -> bool:
        return self.status != "completed"


class EventImporter:
    """Drives a source across a date range, with retries, pacing, dedup and resumable cursors."""

    def __init__(
        self,
        store: Any,
        source: HistoricalSource,
        *,
        clock: Any = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        chunk_days: int = 0,
        sleeper: Any = asyncio.sleep,
    ) -> None:
        self._store = store
        self._source = source
        self._clock = clock
        self._max_retries = max_retries
        self._backoff = retry_backoff_s
        self._min_interval = min_interval_s
        self._page_limit = page_limit
        self._chunk_days = chunk_days
        self._sleep = sleeper
        self._last_request = 0.0

    async def _pace(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            await self._sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    async def _fetch_page(self, **kwargs: Any) -> SourcePage:
        """Bounded retries with exponential backoff; the last failure is raised, not swallowed."""
        attempt = 0
        while True:
            await self._pace()
            try:
                return await self._source.fetch(**kwargs)
            except ImportSourceError as exc:
                attempt += 1
                if not exc.retryable or attempt > self._max_retries:
                    raise
                delay = self._backoff * (2 ** (attempt - 1))
                logger.warning(
                    "archive fetch failed (%s), retry %d/%d in %.1fs",
                    exc, attempt, self._max_retries, delay,
                )  # fmt: skip
                await self._sleep(delay)

    def _windows(self, range_from: date, range_to: date) -> list[tuple[date, date]]:
        if self._chunk_days <= 0:
            return [(range_from, range_to)]
        out: list[tuple[date, date]] = []
        cursor = range_from
        step = timedelta(days=self._chunk_days)
        while cursor <= range_to:
            end = min(cursor + step - timedelta(days=1), range_to)
            out.append((cursor, end))
            cursor = end + timedelta(days=1)
        return out

    async def run(
        self,
        *,
        range_from: date,
        range_to: date,
        symbols: Sequence[str] = (),
        resume: bool = True,
    ) -> ImportReport:
        """Import ``[range_from, range_to]``. Resumable: an interrupted run continues at its cursor."""
        if range_to < range_from:
            raise ValueError("range_to cannot precede range_from")
        prior = self._store.resumable_import(self._source.name, range_from, range_to, symbols) \
            if resume else None  # fmt: skip
        import_id = (
            str(prior["import_id"])
            if prior
            else self._store.begin_import(
                source=self._source.name, range_from=range_from, range_to=range_to, symbols=symbols
            )
        )
        cursor = str(prior["cursor"]) if prior and prior.get("cursor") else None
        report = ImportReport(
            import_id=import_id,
            source=self._source.name,
            status="completed",
            range_from=range_from,
            range_to=range_to,
            symbols=tuple(symbols),
            cursor=cursor,
        )
        if prior:
            report.notes.append(f"resumed import {import_id} at cursor {cursor!r}")

        for window_from, window_to in self._windows(range_from, range_to):
            window_cursor = cursor if cursor else None
            window_records = 0
            answered_any = False
            failure: str | None = None
            while True:
                try:
                    page = await self._fetch_page(
                        range_from=window_from,
                        range_to=window_to,
                        symbols=symbols,
                        cursor=window_cursor,
                        limit=self._page_limit,
                    )
                except (ImportSourceError, SourceNotConfiguredError) as exc:
                    failure = str(exc)
                    break
                answered_any = answered_any or page.answered
                batch = f"{import_id}:{window_from.isoformat()}"
                inserted, duplicates, rejected = self._store.save_source_records(
                    page.records, batch=batch
                )
                report.fetched += len(page.records)
                report.inserted += inserted
                report.duplicates += duplicates
                report.rejected += rejected
                window_records += len(page.records)
                window_cursor = page.next_cursor
                report.cursor = window_cursor
                self._store.update_import_cursor(import_id, window_cursor)
                if not window_cursor or not self._source.supports_pagination:
                    break
                if len(page.records) == 0:
                    report.notes.append(
                        f"{window_from}..{window_to}: a page carried a cursor but no records; "
                        "stopping to avoid an infinite loop"
                    )
                    break

            if failure is not None:
                report.failures += 1
                report.status = "partial"
                report.coverage.append(
                    CoverageWindow(window_from, window_to, "failed", window_records, failure)
                )
                report.notes.append(
                    f"{window_from}..{window_to} FAILED ({failure}); this window is unknown, "
                    "not empty"
                )
            elif not answered_any:
                report.status = "partial"
                report.coverage.append(
                    CoverageWindow(
                        window_from,
                        window_to,
                        "incomplete",
                        0,
                        "the source did not answer for this window",
                    )  # fmt: skip
                )
            else:
                report.coverage.append(
                    CoverageWindow(window_from, window_to, "complete", window_records)
                )
            cursor = None  # a cursor never carries across windows

        if report.failures and report.inserted == 0 and report.duplicates == 0:
            report.status = "failed"
        if report.status != "completed":
            report.notes.append(
                "coverage is incomplete: windows marked failed or incomplete are UNKNOWN, and "
                "must not be read as periods without events"
            )
        self._store.finish_import(report)
        return report
