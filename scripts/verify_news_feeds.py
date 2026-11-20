"""Verify the configured news RSS feeds (owner-run; the only news tool that touches the network).

A feed is fetched only when the registry marks it ``enabled: true`` AND ``verified: <date>``.
This script is how a feed earns that ``verified`` stamp: it runs the same SSRF validation and
bounded fetch the collector uses, parses the feed, and reports what came back - item count, the
newest and oldest publication times, and the feed's own ttl. Nothing is stored.

    uv run python scripts/verify_news_feeds.py            # every feed in the registry
    uv run python scripts/verify_news_feeds.py --key et_markets
    uv run python scripts/verify_news_feeds.py --include-disabled

On success it prints the exact registry edit to make (set ``enabled: true`` and
``verified: "<today>"``). It never writes the registry itself and never prints a credential.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

import httpx2

from src.config import get_settings
from src.marketdata.news import (
    USER_AGENT,
    FeedError,
    FeedSpec,
    parse_news_feed,
    validate_feed_url,
)
from src.ops.exit_codes import ExitCode
from src.ops.process import run_entry_point

MAX_REDIRECTS = 3
# A feed whose newest item is older than this is reachable but abandoned: verification fails it
# rather than stamping dead coverage as live.
STALE_AFTER_DAYS = 7


async def fetch_once(
    feed: FeedSpec, *, timeout_s: float, max_bytes: int
) -> tuple[list, float | None]:
    """One validated, bounded GET (no conditional headers: verification must see real items)."""
    validate_feed_url(feed.url)  # fail before any socket opens
    async with httpx2.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
        url = feed.url
        for _ in range(MAX_REDIRECTS + 1):
            response = await client.request("GET", url, headers={"user-agent": USER_AGENT})
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise FeedError(f"HTTP {response.status_code} without a Location header")
                from urllib.parse import urljoin

                url = urljoin(url, location)
                validate_feed_url(url)  # re-validate every hop
                continue
            if response.status_code != 200:
                raise FeedError(f"HTTP {response.status_code}")
            if len(response.content) > max_bytes:
                raise FeedError(f"response exceeds the {max_bytes}-byte cap")
            entries, ttl_s = parse_news_feed(response.content)
            return entries, ttl_s
        raise FeedError(f"more than {MAX_REDIRECTS} redirects")


async def verify(feed: FeedSpec, *, timeout_s: float, max_bytes: int) -> bool:
    print(f"\n== {feed.key} ({feed.type}) - {feed.name}")
    print(f"   url: {feed.url}")
    state = (
        "enabled+verified"
        if feed.fetchable
        else (
            "owned by " + feed.owned_by
            if feed.owned_by
            else "enabled, NOT verified"
            if feed.enabled
            else "disabled"
        )
    )
    print(f"   registry state: {state}")
    if feed.owned_by is not None:
        print("   skipped: collected by another component; the news collector never fetches it.")
        return True
    try:
        entries, ttl_s = await fetch_once(feed, timeout_s=timeout_s, max_bytes=max_bytes)
    except (FeedError, httpx2.HTTPError, OSError) as exc:
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        print(
            "   -> leave the feed disabled; a feed that cannot be verified is never assumed to work"
        )
        return False
    if not entries:
        print(
            "   fetched OK but parsed 0 items with a usable publication time - treat as NOT "
            "verified (an empty-looking feed may be a format we do not parse)"
        )
        return False
    newest = max(e.published_at for e in entries)
    oldest = min(e.published_at for e in entries)
    age_days = (datetime.now(UTC) - newest).total_seconds() / 86400
    print(
        f"   OK: {len(entries)} item(s), newest {newest.isoformat()}, oldest "
        f"{oldest.isoformat()}, ttl {ttl_s if ttl_s else 'n/a'}"
    )
    print(f"   sample: {entries[0].title[:90]}")
    if age_days > STALE_AFTER_DAYS:
        print(
            f"   STALE: the newest item is {age_days:.0f} day(s) old - the feed is reachable "
            "but appears abandoned. NOT verified: enabling a frozen feed would present dead "
            "coverage as live."
        )
        return False
    today = datetime.now(UTC).astimezone().date().isoformat()
    print(f"   -> to enable, set in src/config/news_feeds.yaml for '{feed.key}':")
    print("        enabled: true")
    print(f'        verified: "{today}"')
    return True


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify news RSS feeds (read-only, no storage).")
    parser.add_argument("--key", action="append", default=[], help="verify only this feed key")
    parser.add_argument("--include-disabled", action="store_true",
                        help="also probe feeds currently disabled in the registry")  # fmt: skip
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    from src.marketdata.news import load_news_feeds

    try:
        feeds = load_news_feeds(settings.news_feeds_file)
    except FeedError as exc:
        print(f"[ERROR] {exc}")
        return ExitCode.CONFIG_ERROR
    wanted = set(args.key)
    selected = [
        f
        for f in feeds
        if (not wanted or f.key in wanted) and (args.include_disabled or f.enabled or f.owned_by)
    ]
    if wanted and not selected:
        print(f"[ERROR] no registry feed matches --key {sorted(wanted)}")
        return ExitCode.CONFIG_ERROR
    if not selected:
        print(
            "no feeds to verify: the registry has none enabled (use --include-disabled to "
            "probe disabled candidates)"
        )
        return ExitCode.OK

    async def run() -> list[bool]:
        return [await verify(f, timeout_s=args.timeout, max_bytes=args.max_bytes) for f in selected]

    results = asyncio.run(run())
    ok = sum(results)
    print(f"\n{ok}/{len(results)} feed(s) verified OK")
    return ExitCode.OK if ok == len(results) else ExitCode.CRASH


if __name__ == "__main__":
    run_entry_point("verify_news_feeds", main)
