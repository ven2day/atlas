"""Readiness check: environment and state directory, enabled LLM roles (exit 2 if one is
misconfigured), the trading universe, Dhan and Telegram status, and configuration warnings.
Never prints a secret."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.config import get_settings
from src.config.errors import ConfigError
from src.llm.registry import validate_roles
from src.ops.exit_codes import ExitCode
from src.ops.process import run_entry_point
from src.reference.universe import load_custom_universe


def main() -> int:
    """Print a readiness check; exit 2 if an LLM role or UNIVERSE_FILE is misconfigured."""
    s = get_settings()

    print("=" * 50)
    print("RakshaQuant - Configuration Check")
    print("=" * 50)

    # LLM roles (plan M6): optional; an enabled role must name a known provider with its key.
    llm_ok = True
    try:
        roles = validate_roles(s)
        enabled = [r for r in roles.values() if r.enabled]
        if not enabled:
            print("LLM roles:        [None enabled - optional]")
        for role in enabled:
            print(f"LLM role {role.role:<8} {' -> '.join(m.spec for m in role.chain)}")
    except ConfigError as exc:
        llm_ok = False
        print(f"LLM roles:        [ERROR] {exc}")

    # Market data provider. Dhan's Data APIs are a paid add-on to the free Trading APIs; the
    # subscription itself is verified against GET /profile when a run starts, never here.
    if s.market_data_provider == "dhan":
        creds = bool(s.dhan_client_id) and (
            s.dhan_access_token is not None
            or (s.dhan_pin is not None and s.dhan_totp_secret is not None)
        )
        print(f"Market data:      dhan    [{'OK' if creds else 'MISSING CREDENTIALS'}]")
        if not creds:
            print(
                "                    needs DHAN_CLIENT_ID plus DHAN_ACCESS_TOKEN, or DHAN_PIN and"
            )
            print("                    DHAN_TOTP_SECRET (see README: 'Dhan market data')")
    else:
        print("Market data:      yfinance (default; MARKET_DATA_PROVIDER=dhan selects DhanHQ)")

    # Trading universe: the NIFTY 50 download, or a hand-written list. Checked here so a bad
    # UNIVERSE_FILE fails now rather than at PRE_OPEN.
    universe_ok = True
    if s.universe_file is None:
        print("Universe:         NIFTY 50 (downloaded from niftyindices.com)")
    else:
        try:
            members = load_custom_universe(s.universe_file, s.universe_sectors)
        except (OSError, ValueError) as exc:
            universe_ok = False
            detail = getattr(exc, "strerror", None) or exc
            print(f"Universe:         [ERROR] UNIVERSE_FILE {s.universe_file.name}: {detail}")
        else:
            print(f"Universe:         file {s.universe_file.name}  [{len(members)} symbols]")
            unsectored = sorted(m.symbol for m in members if not m.industry)
            if unsectored:
                shown = ", ".join(unsectored[:8]) + ("..." if len(unsectored) > 8 else "")
                print(
                    f"                    [WARN] {len(unsectored)} have no sector ({shown}): they"
                )
                print(
                    "                    share risk's 'Unknown' bucket, where RISK_MAX_SECTOR_PCT"
                )
                print("                    caps them together; add them to UNIVERSE_SECTORS")

    # Runtime
    print(f"\nEnvironment:      {s.environment}  (state: {s.state_dir})")
    print(f"Execution Mode:   {s.execution_mode}  (v2 trades on the simulated broker only)")

    # Telegram
    telegram_ok = bool(
        getattr(s, "telegram_bot_token", None) and getattr(s, "telegram_chat_id", None)
    )
    print(f"Telegram Alerts:  {'[OK]' if telegram_ok else '[Not configured - optional]'}")

    # Cross-field validation warnings (an ignored broker venue, Telegram token/chat pairing)
    config_warnings = getattr(s, "config_warnings", [])
    if config_warnings:
        print(f"\nConfiguration Warnings ({len(config_warnings)}):")
        for warning in config_warnings:
            print(f"  [WARN] {warning}")
    else:
        print("\nConfiguration Warnings:  [None]")

    print("\n" + "=" * 50)
    if llm_ok and universe_ok:
        if config_warnings:
            print(f"[READY] System ready to run ({len(config_warnings)} config warning(s) above).")
        else:
            print("[READY] System ready to run!")
    elif not llm_ok:
        print("[ERROR] Fix the LLM role configuration above in .env")
    else:
        print("[ERROR] Fix UNIVERSE_FILE above")
    print("=" * 50)
    return ExitCode.OK if llm_ok and universe_ok else ExitCode.CONFIG_ERROR


if __name__ == "__main__":
    run_entry_point("check_config", main)
