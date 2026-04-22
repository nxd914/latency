"""
Main orchestrator — wires all agents together and runs the event loop.

Usage:
  EXECUTION_MODE=paper python3 daemon.py

Environment variables:
  EXECUTION_MODE    paper (default) | live
  BANKROLL_USDC     starting bankroll (default: 100000.0)
"""

from __future__ import annotations

import sys
import types
import asyncio
import logging
import os
import signal
from pathlib import Path

# Register repo root as 'latency' package when folder name != 'latency'
_repo_root = Path(__file__).resolve().parent
if "latency" not in sys.modules:
    _pkg = types.ModuleType("latency")
    _pkg.__path__ = [str(_repo_root)]  # type: ignore[assignment]
    _pkg.__file__ = str(_repo_root / "__init__.py")
    _pkg.__package__ = "latency"
    _pkg.__spec__ = None  # type: ignore[assignment]
    sys.modules["latency"] = _pkg
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from latency.core.logging import configure_logging


def _load_project_dotenv() -> None:
    """Load `.env` from repo root so the daemon sees API keys without manual export."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    repo_root = Path(__file__).resolve().parent
    env_path = repo_root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    load_dotenv(override=False)


_load_project_dotenv()

from latency.agents import (  # noqa: E402
    CryptoFeedAgent,
    ExecutionAgent,
    FeatureAgent,
    ResolutionAgent,
    RiskAgent,
    ScannerAgent,
    WebsocketAgent,
)
from latency.core.config import Config  # noqa: E402
from latency.core.models import Signal, Tick, TradeOpportunity  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

TRACKED_SYMBOLS: list[str] = os.environ.get("TRACKED_SYMBOLS", "BTC,ETH").split(",")
BANKROLL_USDC = float(os.environ.get("BANKROLL_USDC", "100000.0"))
_SHUTDOWN_TIMEOUT_SECONDS = 10.0
_PID_PATH = Path(__file__).resolve().parent / "data" / "paper_fund.pid"


async def main() -> None:
    config = Config.from_env()

    _PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PID_PATH.write_text(str(os.getpid()))

    if os.environ.get("KALSHI_API_KEY", "").strip():
        logger.info("Kalshi API key present in environment (RSA PEM path must also be set for signing).")
    else:
        logger.warning(
            "KALSHI_API_KEY not set after dotenv load — Kalshi client runs unauthenticated; "
            "place a .env at the repo root (see CLAUDE.md)."
        )

    logger.info(
        "Starting in %s mode | bankroll=%.2f USDC | min_edge=%.2f | max_positions=%d",
        os.environ.get("EXECUTION_MODE", "paper").upper(),
        BANKROLL_USDC,
        config.min_edge,
        config.max_concurrent_positions,
    )

    # Queues
    tick_queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=5000)
    signal_queue: asyncio.Queue[Signal] = asyncio.Queue(maxsize=200)
    scanner_out_queue: asyncio.Queue[TradeOpportunity] = asyncio.Queue(maxsize=100)
    approved_queue: asyncio.Queue[tuple[TradeOpportunity, float]] = asyncio.Queue(maxsize=50)

    # Agents
    crypto_feed = CryptoFeedAgent(tick_queue=tick_queue, symbols=TRACKED_SYMBOLS)
    feature_agent = FeatureAgent(tick_queue=tick_queue, signal_queue=signal_queue)

    ws_agent = WebsocketAgent(
        api_key=os.environ.get("KALSHI_API_KEY", ""),
        private_key_path=os.environ.get("KALSHI_PRIVATE_KEY_PATH", ""),
    )
    scanner = ScannerAgent(
        signal_queue=signal_queue,
        opportunity_queue=scanner_out_queue,
        bankroll_usdc=BANKROLL_USDC,
        price_cache=ws_agent.price_cache,
        crypto_features=feature_agent.latest_features,
        min_edge=config.min_edge,
    )
    risk = RiskAgent(
        opportunity_queue=scanner_out_queue,
        approved_queue=approved_queue,
        bankroll_usdc=BANKROLL_USDC,
        config=config,
    )
    execution = ExecutionAgent(
        approved_queue=approved_queue,
        risk_agent=risk,
    )
    resolver = ResolutionAgent(risk_agent=risk)

    tasks = [
        asyncio.create_task(crypto_feed.run(), name="crypto_feed"),
        asyncio.create_task(feature_agent.run(), name="features"),
        asyncio.create_task(ws_agent.run(), name="kalshi_ws"),
        asyncio.create_task(scanner.run(), name="scanner"),
        asyncio.create_task(risk.run(), name="risk"),
        asyncio.create_task(execution.run(), name="execution"),
        asyncio.create_task(resolver.run(), name="resolver"),
    ]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [t.cancel() for t in tasks])

    logger.info("All agents running (%d tasks). Press Ctrl+C to stop.", len(tasks))
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutdown signal received — stopping agents.")
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            still_running = [t.get_name() for t in tasks if not t.done()]
            logger.warning("Shutdown timed out after %.0fs — tasks still running: %s", _SHUTDOWN_TIMEOUT_SECONDS, still_running)
    finally:
        _PID_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
