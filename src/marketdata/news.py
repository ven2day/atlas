"""Configurable public news RSS collection (event-driven research).

Complements - never replaces - :mod:`src.marketdata.announcements`: the NSE exchange feed stays
owned by the announcement ingestor (its feeds are marked ``owned_by`` in the registry and are
skipped here, so nothing double-polls the exchange or re-interprets its terms). This module adds
*media and macro* feeds from a registry the operator controls (``src/config/news_feeds.yaml``).

Safety and honesty rules, mirroring the announcement ingestor's:

* **A feed is fetched only when enabled AND verified** (``scripts/verify_news_feeds.py``); an
  unverified URL stays disabled rather than being silently assumed to work.
* **SSRF-guarded**: http/https only, no credentials in the URL, every host resolved and refused
  when it points at a private/loopback/link-local/reserved address, and every redirect hop
  re-validated (bounded at 3). Responses are streamed with a hard byte cap and a timeout.
* **Point in time**: the publisher's ``published_at`` and our ``received_at`` are separate facts;
  nothing is backdated. Items without a parseable publication time are skipped, never stamped
  with our clock.
* **Provenance under duplication**: the same story from two feeds keeps both records; the later
  one carries ``duplicate_of`` pointing at the first-seen ``news_id``.
* **A failed feed is never an empty feed**: the first failure of an episode raises a WARNING
  alert and records one ``NewsCoverageGap`` (from the last success, or None when the feed never
  succeeded); recovery raises an INFO alert.
* Only headline, a short summary, timestamps and the article link are stored - never full text.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx2
import yaml

from src.domain.clock import Clock
from src.domain.events import Alert, NewsCoverageGap, NewsItemReceived, NewsSymbolMatch
from src.domain.sink import EventSink
from src.domain.types import Instrument
from src.utils.market_time import IST

logger = logging.getLogger(__name__)

DEFAULT_FEEDS_FILE = Path(__file__).resolve().parents[1] / "config" / "news_feeds.yaml"
USER_AGENT = "RakshaQuant/2 (paper-trading research; RSS reader)"
SAFE_SCHEMES = ("http", "https")
MAX_REDIRECTS = 3
SUMMARY_LIMIT = 600
TITLE_LIMIT = 500
FEED_TYPES = ("media_news", "macro", "exchange_filing")
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SUFFIXES = ("limited", "ltd", "the")


class FeedError(RuntimeError):
    """A feed could not be fetched or parsed. Always reported, never read as 'no news'."""


class UnsafeFeedURLError(FeedError):
    """The URL failed validation: wrong scheme, credentials, or a non-public destination."""


# -- registry ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedSpec:
    key: str
    name: str
    type: str  # media_news | macro | exchange_filing
    url: str
    enabled: bool = False
    verified: str | None = None  # the date scripts/verify_news_feeds.py last succeeded
    min_interval_s: float = 300.0
    owned_by: str | None = None  # e.g. "announcements": fetched elsewhere, never by this module

    @property
    def fetchable(self) -> bool:
        """Enabled, verified, and not owned by another collector."""
        return self.enabled and bool(self.verified) and self.owned_by is None


def load_news_feeds(path: Path | None = None) -> tuple[FeedSpec, ...]:
    """Parse the registry. A malformed registry raises rather than yielding zero feeds: a
    configuration error must never masquerade as 'no feeds configured'."""
    file = path or DEFAULT_FEEDS_FILE
    try:
        document = yaml.safe_load(file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FeedError(f"cannot read the news feed registry {file}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise FeedError(f"the news feed registry {file} is not valid YAML: {exc}") from exc
    entries = (document or {}).get("feeds")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise FeedError("the registry's 'feeds' must be a list")
    feeds: list[FeedSpec] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise FeedError(f"registry entry is not a mapping: {entry!r}")
        key = str(entry.get("key") or "").strip()
        name = str(entry.get("name") or "").strip()
        kind = str(entry.get("type") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not key or not name or not url or kind not in FEED_TYPES:
            raise FeedError(
                f"registry entry needs key, name, url and a type in {FEED_TYPES}: {entry!r}"
            )
        if key in seen:
            raise FeedError(f"duplicate feed key {key!r} in the registry")
        seen.add(key)
        verified = entry.get("verified")
        feeds.append(
            FeedSpec(
                key=key,
                name=name,
                type=kind,
                url=url,
                enabled=bool(entry.get("enabled", False)),
                verified=str(verified) if verified not in (None, "") else None,
                min_interval_s=float(entry.get("min_interval_s", 300.0)),
                owned_by=str(entry["owned_by"]) if entry.get("owned_by") else None,
            )
        )
    return tuple(feeds)


# -- URL safety ------------------------------------------------------------------------------------


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_feed_url(
    url: str, *, resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo
) -> None:
    """Refuse anything that is not a plain public http(s) URL. ``resolver`` is injectable so the
    check is testable without DNS."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UnsafeFeedURLError(f"unparseable feed URL: {exc}") from exc
    if parsed.scheme not in SAFE_SCHEMES:
        raise UnsafeFeedURLError(
            f"feed URL scheme {parsed.scheme!r} is not allowed (http/https only)"
        )
    if parsed.username or parsed.password:
        raise UnsafeFeedURLError("feed URLs must not carry credentials")
    host = parsed.hostname
    if not host:
        raise UnsafeFeedURLError("feed URL has no host")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = resolver(host, None)
        except (socket.gaierror, UnicodeError) as exc:
            raise FeedError(f"cannot resolve the feed host {host!r}: {exc}") from exc
        ips = {ipaddress.ip_address(str(info[4][0])) for info in infos}
        if not ips:
            raise FeedError(f"the feed host {host!r} resolves to no address")
    else:
        ips = {literal}
    blocked = [str(ip) for ip in ips if not _is_public(ip)]
    if blocked:
        raise UnsafeFeedURLError(
            f"the feed host {host!r} resolves to a non-public address ({', '.join(sorted(blocked))}); "
            "refusing to fetch"
        )


# -- parsing -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsEntry:
    """One item as the feed published it (never as we would like it)."""

    guid: str | None
    title: str
    summary: str
    url: str | None
    published_at: datetime  # aware


def _clean(text: str, limit: int) -> str:
    return " ".join(_TAG.sub(" ", text).split())[:limit]


def _parse_published(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)  # RFC 822 (RSS pubDate)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))  # Atom
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)


def parse_news_feed(body: bytes) -> tuple[list[NewsEntry], float | None]:
    """RSS 2.0 and Atom entries, plus the feed's own ttl in seconds. Malformed XML raises;
    an item without a parseable publication time is skipped (its age cannot be stated)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FeedError(f"the feed is not XML: {exc}") from exc
    ttl_s: float | None = None
    entries: list[NewsEntry] = []
    channel = root.find("channel")
    if channel is not None:
        ttl = channel.findtext("ttl")
        try:
            ttl_s = float(ttl) * 60 if ttl else None
        except ValueError:
            ttl_s = None
        nodes = channel.findall("item")
        for node in nodes:
            title = _clean(node.findtext("title") or "", TITLE_LIMIT)
            published = _parse_published(node.findtext("pubDate") or "")
            if not title or published is None:
                continue
            link = (node.findtext("link") or "").strip() or None
            guid = (node.findtext("guid") or "").strip() or None
            summary = _clean(node.findtext("description") or "", SUMMARY_LIMIT)
            entries.append(NewsEntry(guid, title, summary, link, published))
    else:
        nodes = root.findall(f"{_ATOM}entry")
        for node in nodes:
            title = _clean(node.findtext(f"{_ATOM}title") or "", TITLE_LIMIT)
            when = node.findtext(f"{_ATOM}published") or node.findtext(f"{_ATOM}updated") or ""
            published = _parse_published(when)
            if not title or published is None:
                continue
            link = None
            for candidate in node.findall(f"{_ATOM}link"):
                rel = candidate.get("rel") or "alternate"
                if rel == "alternate" and candidate.get("href"):
                    link = candidate.get("href")
                    break
            guid_node = node.find(f"{_ATOM}id")
            guid = (
                (guid_node.text or "").strip() if guid_node is not None and guid_node.text else None
            )
            summary = _clean(
                node.findtext(f"{_ATOM}summary") or node.findtext(f"{_ATOM}content") or "",
                SUMMARY_LIMIT,
            )
            entries.append(NewsEntry(guid, title, summary, link, published))
    return entries, ttl_s


# -- symbol matching ---------------------------------------------------------------------------------


def normalise_name(name: str) -> str:
    text = _PUNCT.sub(" ", name.lower().replace("&", " and "))
    return " ".join(w for w in text.split() if w not in _SUFFIXES)


class NewsSymbolMatcher:
    """Universe symbols in a headline/summary, with the evidence - and an explicit unresolved
    answer when a mention is ambiguous. A guessed instrument would price the wrong company."""

    def __init__(self, instruments: Iterable[Instrument], max_matches: int = 5) -> None:
        self._by_name: dict[str, set[str]] = {}
        self._keys: dict[str, Instrument] = {}
        self._symbols: dict[str, Instrument] = {}
        self._max = max_matches
        for instrument in instruments:
            self._keys[instrument.key] = instrument
            if instrument.symbol:
                self._symbols[instrument.symbol.upper()] = instrument
            for name in (instrument.name, instrument.symbol):
                if name and len(normalise_name(name)) >= 4:
                    self._by_name.setdefault(normalise_name(name), set()).add(instrument.key)
        # Word-boundary patterns: a plain substring test would match "INFY" inside "INFYX" and
        # price the wrong company. Names are matched on the normalised text, symbols on the raw.
        self._name_patterns = {
            name: re.compile(rf"\b{re.escape(name)}\b") for name in self._by_name
        }
        self._symbol_patterns = {
            symbol: re.compile(rf"\b{re.escape(symbol)}\b") for symbol in self._symbols
        }

    def match(self, title: str, summary: str) -> tuple[NewsSymbolMatch, ...]:
        found: dict[str, NewsSymbolMatch] = {}
        title_norm = normalise_name(title)
        summary_norm = normalise_name(summary)
        for name, keys in self._by_name.items():
            pattern = self._name_patterns[name]
            where = (
                "the title"
                if pattern.search(title_norm)
                else "the summary"
                if pattern.search(summary_norm)
                else None
            )
            if where is None:
                continue
            if len(keys) > 1:  # ambiguous company mention: unresolved, never guessed
                found.setdefault(
                    name,
                    NewsSymbolMatch(
                        symbol=name,
                        instrument_key=None,
                        evidence=f"name '{name}' in {where} matches {len(keys)} "
                        "instruments - left unresolved",
                        resolved=False,
                    ),
                )
                continue
            key = next(iter(keys))
            found.setdefault(key, NewsSymbolMatch(
                symbol=self._keys[key].symbol, instrument_key=key,
                evidence=f"company name '{name}' in {where}",
            ))  # fmt: skip
        for symbol, instrument in self._symbols.items():
            if len(symbol) < 3:
                continue  # two-letter fragments produce false positives
            pattern = self._symbol_patterns[symbol]
            where = (
                "the title"
                if pattern.search(title)
                else "the summary"
                if pattern.search(summary)
                else None
            )
            if where is None or instrument.key in found:
                continue
            found[instrument.key] = NewsSymbolMatch(
                symbol=symbol, instrument_key=instrument.key,
                evidence=f"symbol {symbol} in {where}",
            )  # fmt: skip
        return tuple(list(found.values())[: self._max])


# -- the collector ------------------------------------------------------------------------------------


def news_id_of(feed_key: str, entry: NewsEntry) -> str:
    basis = entry.guid or entry.url or entry.title
    digest = hashlib.sha256(f"{feed_key}|{basis}|{entry.published_at.isoformat()}".encode())
    return digest.hexdigest()[:24]


def content_hash_of(entry: NewsEntry) -> str:
    digest = hashlib.sha256(normalise_name(entry.title).encode())
    return digest.hexdigest()[:24]


@dataclass
class FeedStats:
    key: str
    ok: bool
    fetched: int = 0
    new: list[NewsItemReceived] = field(default_factory=list)
    not_modified: bool = False
    error: str | None = None


@dataclass
class NewsPollStats:
    ok: bool
    feeds: list[FeedStats] = field(default_factory=list)
    new: list[NewsItemReceived] = field(default_factory=list)


class NewsCollector:
    """Polls the registry's fetchable feeds no faster than each feed asks, with bounded
    concurrency, conditional GET, dedupe and coverage gaps."""

    def __init__(
        self,
        *,
        feeds: Sequence[FeedSpec],
        instruments: Iterable[Instrument],
        clock: Clock,
        sink: EventSink,
        client: httpx2.AsyncClient | None = None,
        timeout_s: float = 15.0,
        max_bytes: int = 2_000_000,
        concurrency: int = 3,
        seen: Iterable[str] = (),
        by_content: Mapping[str, str] | None = None,
        last_ok: Mapping[str, datetime] | None = None,
    ) -> None:
        self._feeds = [f for f in feeds if f.fetchable]
        self._matcher = NewsSymbolMatcher(instruments)
        self._clock = clock
        self._sink = sink
        self._client = client or httpx2.AsyncClient(timeout=timeout_s, follow_redirects=False)
        self._timeout = timeout_s
        self._max_bytes = max_bytes
        self._concurrency = concurrency
        self._seen = set(seen)
        self._by_content = dict(by_content or {})
        self._last_ok = dict(last_ok or {})
        self._validators: dict[str, dict[str, str]] = {}
        self._last_fetch: dict[str, datetime] = {}
        self._failing_since: dict[str, datetime] = {}
        self._gap_open: set[str] = set()
        self._intervals = {f.key: f.min_interval_s for f in self._feeds}

    @property
    def interval_s(self) -> float:
        return min(self._intervals.values(), default=300.0)

    async def poll(self, *, force: bool = False) -> NewsPollStats:
        now = self._clock.now()
        due = [
            f
            for f in self._feeds
            if force
            or f.key not in self._last_fetch
            or now - self._last_fetch[f.key] >= timedelta(seconds=self._intervals[f.key])
        ]
        if not due:
            return NewsPollStats(ok=True)
        semaphore = asyncio.Semaphore(self._concurrency)
        results = await asyncio.gather(*(self._poll_feed(f, now, semaphore) for f in due))
        stats = NewsPollStats(ok=all(r.ok for r in results), feeds=list(results))
        for result in results:
            stats.new.extend(result.new)
        return stats

    async def _poll_feed(
        self, feed: FeedSpec, now: datetime, semaphore: asyncio.Semaphore
    ) -> FeedStats:
        async with semaphore:
            self._last_fetch[feed.key] = now
            try:
                fetched = await self._fetch(feed)
            except (httpx2.HTTPError, FeedError) as exc:
                return self._failed(feed, now, f"{type(exc).__name__}: {exc}")
            if fetched is None:
                self._recovered(feed, now)
                return FeedStats(key=feed.key, ok=True, not_modified=True)
            body, headers, ttl_s = fetched
            self._validators[feed.key] = {
                k: v
                for k, v in (
                    ("if-none-match", headers.get("etag")),
                    ("if-modified-since", headers.get("last-modified")),
                )
                if v
            }
            try:
                entries, feed_ttl = parse_news_feed(body)
            except FeedError as exc:
                return self._failed(feed, now, str(exc))
            effective = ttl_s if ttl_s and ttl_s > feed.min_interval_s else None
            if effective is None and feed_ttl and feed_ttl > self._intervals.get(feed.key, 0):
                effective = feed_ttl
            if effective:
                self._intervals[feed.key] = min(effective, 3600.0)  # the feed's own pace, capped
            self._recovered(feed, now)
            self._last_ok[feed.key] = now
            stats = FeedStats(key=feed.key, ok=True, fetched=len(entries))
            for entry in sorted(entries, key=lambda e: e.published_at):
                item = self._accept(feed, entry, now)
                if item is not None:
                    stats.new.append(item)
            return stats

    async def _fetch(self, feed: FeedSpec) -> tuple[bytes, Mapping[str, str], float | None] | None:
        """One validated, bounded, conditional GET. ``None`` = 304 not modified."""
        url = feed.url
        await asyncio.to_thread(validate_feed_url, url)
        for _ in range(MAX_REDIRECTS + 1):
            headers = {"user-agent": USER_AGENT, **self._validators.get(feed.key, {})}
            response = await self._client.request(
                "GET", url, headers=headers, timeout=self._timeout, follow_redirects=False
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise FeedError(f"HTTP {response.status_code} without a Location header")
                url = urljoin(url, location)
                await asyncio.to_thread(validate_feed_url, url)  # re-validate every hop
                continue
            if response.status_code == 304:
                return None
            if response.status_code != 200:
                raise FeedError(f"HTTP {response.status_code}")
            body = response.content
            if len(body) > self._max_bytes:
                raise FeedError(f"response exceeds the {self._max_bytes}-byte cap")
            return body, response.headers, None
        raise FeedError(f"more than {MAX_REDIRECTS} redirects")

    def _accept(self, feed: FeedSpec, entry: NewsEntry, now: datetime) -> NewsItemReceived | None:
        news_id = news_id_of(feed.key, entry)
        if news_id in self._seen:
            return None
        content = content_hash_of(entry)
        first_seen = self._by_content.get(content)
        item = NewsItemReceived(
            news_id=news_id,
            source_key=feed.key,
            source_name=feed.name,
            source_type=feed.type,
            title=entry.title,
            summary=entry.summary,
            url=entry.url,
            published_at=entry.published_at,
            received_at=now,  # the truth: when *we* saw it, never the publication time
            symbols=self._matcher.match(entry.title, entry.summary),
            content_hash=content,
            duplicate_of=first_seen if first_seen and first_seen != news_id else None,
        )
        self._seen.add(news_id)
        self._by_content.setdefault(content, news_id)
        self._sink.emit(item, source="news")
        return item

    def _failed(self, feed: FeedSpec, now: datetime, error: str) -> FeedStats:
        logger.warning("news feed %s failed: %s", feed.key, error)
        first = feed.key not in self._failing_since
        if first:
            self._failing_since[feed.key] = now
            self._sink.emit(
                Alert(level="WARNING", key=f"news_feed_down:{feed.key}",
                      message=f"news feed '{feed.name}' is unavailable: {error}"),
                source="news",
            )  # fmt: skip
        if feed.key not in self._gap_open:
            # One gap per failure episode: the window from the last success (None when the feed
            # never succeeded) is UNKNOWN coverage, never "no news".
            self._gap_open.add(feed.key)
            self._sink.emit(
                NewsCoverageGap(source_key=feed.key, gap_from=self._last_ok.get(feed.key),
                                gap_to=now, reason=error),
                source="news",
            )  # fmt: skip
        return FeedStats(key=feed.key, ok=False, error=error)

    def _recovered(self, feed: FeedSpec, now: datetime) -> None:
        self._gap_open.discard(feed.key)
        if feed.key in self._failing_since:
            del self._failing_since[feed.key]
            self._sink.emit(
                Alert(level="INFO", key=f"news_feed_recovered:{feed.key}",
                      message=f"news feed '{feed.name}' is answering again"),
                source="news",
            )  # fmt: skip

    async def close(self) -> None:
        await self._client.aclose()


def news_watermark(
    events: Iterable[NewsItemReceived],
) -> tuple[set[str], dict[str, str], dict[str, datetime]]:
    """Restart state: seen ids, content-hash -> first news_id, and each feed's last success."""
    seen: set[str] = set()
    by_content: dict[str, str] = {}
    last_ok: dict[str, datetime] = {}
    ordered = sorted(events, key=lambda e: e.received_at)
    for item in ordered:
        seen.add(item.news_id)
        by_content.setdefault(item.content_hash, item.news_id)
        prior = last_ok.get(item.source_key)
        if prior is None or item.received_at > prior:
            last_ok[item.source_key] = item.received_at
    return seen, by_content, last_ok
