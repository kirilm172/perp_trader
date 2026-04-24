# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the repo root.

```bash
# Install dependencies
uv sync --locked

# Run the bot
uv run main.py
```

Docker (from repo root):
```bash
./scripts/build.sh   # docker build -t my-perp-bot .
./scripts/run.sh     # docker run --env-file .env my-perp-bot
```

## Architecture

This is an async perpetual futures **cross-exchange arbitrage bot**. It simultaneously monitors orderbooks across multiple exchanges, detects spread opportunities, and places coordinated buy/sell orders.

### Data flow

```
DataFeed → (SpreadData queue) → Strategy → ArbitragePosition lifecycle
                                         → UIRenderer (Rich dashboard)
```

All modules extend `BaseModule` (`modules/base.py`), which wraps an `asyncio.TaskGroup` and standardizes lifecycle logging. Each module implements `get_tasks()` returning coroutines.

### Key modules

| Module | Responsibility |
|---|---|
| `main.py` | Entry point; initializes CCXT exchanges, loads markets by volume, launches three concurrent task groups |
| `modules/data_feed.py` | Watches orderbooks via CCXT `watch_order_book`; calculates weighted average prices accounting for position size and slippage; emits `SpreadData` |
| `modules/strategy.py` | Consumes `SpreadData`; computes `net_spread = raw_spread - commission`; opens positions when `net_spread > open_position_net_spread_threshold`; closes when below `close_position_raw_spread_threshold` or timeout |
| `modules/arbitrage_position.py` | Encapsulates a single open position (buy on exchange A, sell on exchange B); handles order creation, amount precision, trailing stops, and close logic |
| `modules/ui_renderer.py` | Rich-based terminal dashboard; top-30 spread table and open positions panel |
| `settings.py` | `BotConfig` and `PositionConfig` dataclasses; all thresholds and tuning knobs live here |

### Spread math

```
raw_spread  = (sell_price - buy_price) / mid_price * 100   # percent
commission  = taker_fee × 4   # open long + open short + close long + close short
net_spread  = raw_spread - commission
```

A position is opened when `net_spread > open_position_net_spread_threshold` (default 0.1%) and closed when `raw_spread < close_position_raw_spread_threshold` (default 0.02%) or after `close_position_after_seconds` (default 3 h).

### Configuration

`settings.py` (`BotConfig`) is the single place for tuning. Notable fields:
- `top_n_markets` — number of markets monitored (currently 100)
- `orderbook_depths` — per-exchange order book depth
- `analyze_arbitrage_max_data_age_ms` — stale-data cutoff (200 ms)
- `adaptive_thresholds` — adjusts thresholds by volatility
- `use_ui` / `use_profiler` / `debug` — observability toggles

### Environment

Credentials are loaded from `.env`:
```
BINANCE_API_KEY, BINANCE_SECRET
BYBIT_API_KEY, BYBIT_SECRET
OKX_API_KEY, OKX_SECRET, OKX_PASSWORD
```

Supported exchanges: Binance, Bybit, OKX (configured in `main.py`).
