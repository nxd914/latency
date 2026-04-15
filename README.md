[![Python](https://img.shields.io/badge/python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Kalshi](https://img.shields.io/badge/exchange-Kalshi-0a1628.svg)](https://kalshi.com)
[![Status](https://img.shields.io/badge/status-paper_trading-yellow.svg)](#usage)
[![Tests](https://img.shields.io/badge/tests-pytest-009688.svg?logo=pytest&logoColor=white)](./tests)
[![Internal](https://img.shields.io/badge/repo-internal-red.svg)](https://github.com/nxd914/quant)

Internal automated trading system on Kalshi (CFTC-regulated binary prediction markets).
Edge: crypto spot-price propagation latency between global CEX feeds (Binance.US / Coinbase) and Kalshi BTC/ETH probability contracts. Deterministic Black-Scholes N(d2) pricing, Kelly-capped sizing, zero learned parameters in the execution path.

> Source: [github.com/nxd914/quant](https://github.com/nxd914/quant)

## Architecture

```
CryptoFeedAgent (Binance.US + Coinbase WS)
        ↓ tick_queue
FeatureAgent (Welford rolling windows — 60s signal, 15min pricing)
        ↓ signal_queue
WebsocketAgent (Kalshi WS price cache)
        ↓
ScannerAgent (N(d2) / bracket_prob scoring, edge filter)
        ↓
RiskAgent (Kelly + position limits + proactive exposure gate)
        ↓
ExecutionAgent (paper/live, SQLite audit trail)
        ↓
ResolutionAgent (settlement polling, P&L close)
```

## Repository layout

```
agents/       Async agent loop (crypto feed, features, scanner, risk, execution, resolution)
core/         Pure math + exchange client (kelly, pricing, kalshi_client, models, features)
tools/        CLI + paper runner + dashboard
research/     Offline analysis (health check, P&L dashboard, edge analysis, market scans)
tests/        pytest suite
scripts/      run.sh lifecycle manager, AWS setup, force_resolve
deploy/       Dockerfile, docker-compose
docs/         Internal notes
data/         SQLite trade DB, logs, PID files (gitignored)
```

## Setup

```bash
pip install -r requirements.txt

mkdir -p ~/.quant
openssl genrsa -out ~/.quant/kalshi_private.pem 2048
openssl rsa -in ~/.quant/kalshi_private.pem -pubout -out ~/.quant/kalshi_public.pem
```

Upload the public key in the Kalshi dashboard, copy the key UUID.

## Environment

| Var | Required | Notes |
|-----|----------|-------|
| `KALSHI_API_KEY` | yes | UUID from Kalshi dashboard |
| `KALSHI_PRIVATE_KEY_PATH` | yes | `~/.quant/kalshi_private.pem` |
| `EXECUTION_MODE` | no | `paper` (default) / `live` |
| `BANKROLL_USDC` | no | default `100000` |

## Usage

```bash
./scripts/run.sh start|stop|restart|status    # daemon lifecycle
python3 -m quant.daemon                       # run daemon in foreground
python3 -m research.health_check              # live P&L + process health
```

## Risk controls

| Control | Value |
|---------|-------|
| Kelly cap | 0.25× |
| Min edge | 4% |
| Max concurrent positions | 5 |
| Max single exposure | 10% of bankroll |
| Max positions per symbol | 2 |
| Daily loss gate | 20% of bankroll (proactive — pending exposure counted) |
| Min NO fill price | 0.40 |
| Max hours to expiry | 4 |
| Min seconds between fills | 30 |
| Spread floor | 4% |
| Bracket calibration haircut | 45% |
| Live trading gate | disabled until paper Sharpe ≥ 1.0 over 2+ weeks |
