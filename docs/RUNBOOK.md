# Kinzie Runbook

Operational procedures for the Kinzie trading daemon.

---

## Quick reference

| Situation | Action |
|-----------|--------|
| Daemon won't start | Check `KALSHI_API_KEY` and `KALSHI_PRIVATE_KEY_PATH` — see [Env var failures](#env-var-failures) |
| Daily loss circuit breaker fired | See [Daily loss halt recovery](#daily-loss-halt-recovery) |
| Consecutive-loss halt | See [Consecutive-loss halt recovery](#consecutive-loss-halt-recovery) |
| Stuck open position | See [Position-stuck recovery](#position-stuck-recovery) |
| Feed disconnected | Daemon auto-reconnects — see [Feed disconnects](#feed-disconnects) |
| DB corruption | Stop daemon, restore from backup, restart |

---

## Starting and stopping

```bash
cd /path/to/kinzie

# Start (paper mode, default)
PYTHONPATH=.. python3 daemon.py

# Start with script (handles PID, log rotation)
bash scripts/run.sh start

# Stop gracefully
bash scripts/run.sh stop

# Restart
bash scripts/run.sh restart

# Check status
bash scripts/run.sh status
```

The daemon sends SIGTERM on `Ctrl+C`. All agents receive `CancelledError` and have up to 10 seconds to complete in-flight operations before the process exits.

---

## Environment variables

| Variable | Required | Default | Check |
|----------|----------|---------|-------|
| `KALSHI_API_KEY` | Yes | — | `echo $KALSHI_API_KEY` |
| `KALSHI_PRIVATE_KEY_PATH` | Yes | — | `ls -la $KALSHI_PRIVATE_KEY_PATH` |
| `BANKROLL_USDC` | No | 100000 | — |
| `EXECUTION_MODE` | No | paper | Must be `paper` |
| `LOG_FORMAT` | No | plain | Set `json` for structured logs |
| `LOG_LEVEL` | No | INFO | Set `DEBUG` for verbose output |

### Env var failures

**`KALSHI_API_KEY` not set:**
```
WARNING Kalshi API key not set after dotenv load
```
→ Check `.env` at repo root. Key should be a UUID from the Kalshi dashboard.

**`KALSHI_PRIVATE_KEY_PATH` not set or file missing:**
→ Daemon will start but WebSocket auth will fail at connect. Generate key:
```bash
mkdir -p ~/.latency
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
    -out ~/.latency/private.pem
# Upload the public key to Kalshi dashboard → API settings
```

---

## Circuit breakers

### Daily loss halt recovery

**Symptom:** Log line containing `daily loss circuit breaker` or `DAILY_LOSS_HALT`.
All new positions are blocked until midnight UTC.

**What happened:** Realized P&L for the current day dropped below `-20%` of bankroll
(configured as `max_daily_loss_pct = 0.20` in `core/config.py`).

**Recovery:**
1. Do **not** restart the daemon — the halt resets at midnight UTC automatically.
2. Inspect recent fills: `python3 -m research.health_check`
3. Review the calibration: `python3 -m research.replay_backtest`
4. If you believe the halt was triggered in error (e.g., P&L calculation bug),
   stop the daemon and clear daily P&L state by restarting at midnight.

**Do NOT lower `max_daily_loss_pct` to bypass this gate** — it exists to limit correlated
loss scenarios where the model's edge has temporarily disappeared.

---

### Consecutive-loss halt recovery

**Symptom:** Log line containing `consecutive_loss_halt` or `3 consecutive losses`.
All new positions are blocked for 24 hours from the last fill.

**What happened:** The last 3 resolved fills were all losses.

**Recovery:**
1. Do **not** restart to bypass the halt — it will re-engage immediately.
2. Wait 24 hours from the last fill timestamp.
3. Review fill history: `python3 -m research.edge_analysis` (if available)
4. If the model's edge has decayed, reduce `min_edge` or pause trading until
   the calibration is re-examined.

---

## Position-stuck recovery

**Symptom:** A position remains open (status=`filled`) in the DB days after expiry.
The ResolutionAgent polls for resolution but Kalshi hasn't returned a result yet.

**Diagnosis:**
```bash
sqlite3 data/paper_trades.db \
    "SELECT order_id, ticker, side, status, placed_at FROM trades WHERE status='filled';"
```

**Recovery using `scripts/force_resolve.py`:**
```bash
PYTHONPATH=.. python3 scripts/force_resolve.py --order-id <order_id> --outcome YES
```
This calls `RiskAgent.restore_position()` to release the slot and records the resolution
in the audit trail. **Only use after confirming the Kalshi contract has resolved.**

---

## Feed disconnects

The CryptoFeedAgent and WebsocketAgent both implement exponential-backoff reconnect:
- Initial delay: 1 second
- Max delay: 60 seconds
- Reset on successful message

**Symptoms in log:**
```
ERROR Feed error for BTC: ... — retrying in 2.0s
ERROR Kalshi WebSocket error: ... — reconnecting in 4.0s
```

**What to do:** Nothing. Reconnects are automatic. Monitor the log for sustained failure
(>5 minutes of retries without success), which would indicate an API outage or IP block.

**If feeds are down for >10 minutes:**
1. Check Kalshi status page.
2. Check Binance.US and Coinbase API status.
3. Verify `KALSHI_API_KEY` is not rate-limited (429 responses).
4. Restart daemon if WebSocket is in a bad state.

---

## Logs

**Plain format (default):**
```
2026-04-19T14:22:01 latency.agents.scanner INFO Found 3 candidates above edge floor
```

**JSON format** (set `LOG_FORMAT=json`):
```json
{"timestamp": "2026-04-19T14:22:01Z", "level": "info", "logger": "latency.agents.scanner", "event": "Found 3 candidates above edge floor"}
```

**Log files** (via `scripts/run.sh`): `logs/daemon.log` — rotated at 10 MB, 5 files kept.

**Useful greps:**
```bash
grep "CIRCUIT_BREAKER\|circuit breaker\|halt" logs/daemon.log
grep "ERROR\|WARNING" logs/daemon.log | tail -50
grep "filled\|resolved" logs/daemon.log | tail -20
grep "scanner.*opportunity" logs/daemon.log | tail -10
```

---

## P&L and health checks

```bash
# Process health + recent trade freshness
python3 -m research.health_check

# Full P&L dashboard
python3 -m research.pnl_dashboard

# Audit-trail replay backtester (calibration + Sharpe)
python3 -m research.replay_backtest

# Hot-path latency profile
python3 -m benchmarks.hot_path
```

---

## Database

**Location:** `data/paper_trades.db` (gitignored).

**Schema inspection:**
```bash
sqlite3 data/paper_trades.db ".schema trades"
```

**Key columns:** `order_id`, `ticker`, `side`, `model_prob`, `market_prob`, `edge`,
`size_usdc`, `fill_price`, `status`, `spot_price_at_signal`, `signal_latency_ms`,
`realized_vol`, `kelly_fraction`, `resolved_at`, `resolution`, `pnl_usdc`.

**Backup:**
```bash
cp data/paper_trades.db data/paper_trades_$(date +%Y%m%d).db
```

---

## Live mode gate

Live mode is permanently disabled until both conditions are met:
- `min_fills_for_live = 100` resolved paper fills
- `min_sharpe_for_live = 1.0` rolling Sharpe over all fills

Check current status:
```bash
python3 -m research.replay_backtest
```

Setting `EXECUTION_MODE=live` before these thresholds raises `NotImplementedError`.
This is intentional — do not remove the gate.
