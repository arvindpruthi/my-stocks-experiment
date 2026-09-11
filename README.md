# my-stocks-experiment
Stock Experiment

A Python implementation of the Nasdaq-100 tech quant trading strategy documented in
[`stock-trading-strategy.md`](stock-trading-strategy.md). The strategy scores a static
universe of Nasdaq-100 tech/communication-services names on trend, fundamentals, and
news-risk pillars, builds a risk-managed target portfolio, and can either backtest that
logic or generate a live (no-order-execution) signal report.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Market data (prices + fundamentals) comes from Yahoo Finance via `yfinance` and is cached
on disk under `.cache/market_data/` (gitignored) to avoid re-downloading on every run.

## Running

Everything goes through `trading_strategy.py`. It has three mutually exclusive modes:

```bash
python trading_strategy.py --list-universe   # print the tracked tickers + benchmark
python trading_strategy.py --test-history    # backtest vs. buy-and-hold QQQ
python trading_strategy.py --run             # today's regime/scores/target portfolio (no trades placed)
```

**Note:** `trading_strategy.py` depends on packages installed inside `.venv` (numpy,
yfinance, etc.). Running it with a bare `python`/`python3` from a shell where the venv
isn't activated will fail with `ModuleNotFoundError`. Either activate the venv first
(`source .venv/bin/activate`) or use the `run.sh` wrapper below, which activates it for you
(and creates it on first use if it doesn't exist yet).

```bash
./run.sh --list-universe
./run.sh --test-history --quarters 20 --capital 25000
./run.sh --run --holdings my_holdings.json --output today.json
```

### Examples

```bash
python trading_strategy.py --list-universe
python trading_strategy.py --test-history
python trading_strategy.py --test-history --quarters 20 --capital 25000
python trading_strategy.py --run
python trading_strategy.py --run --holdings my_holdings.json --output today.json
```

### Flags

| Flag | Description |
| --- | --- |
| `--test-history` | Backtest the strategy over N quarters (default 50) vs. buy-and-hold QQQ. |
| `--run` | Generate today's regime/scores/target portfolio (live, no trades placed). |
| `--list-universe` | Print the tracked ticker universe and exit. |
| `--quarters N` | Backtest window length in quarters (default: 50). |
| `--capital N` | Starting capital in dollars (default: 10000). |
| `--rebalance {weekly,daily}` | How often to fully re-score and rebalance in the backtest (default: weekly). |
| `--no-fundamentals` | Disable the fundamental score pillar. |
| `--no-news` | Disable the live news overlay in `--run` mode. |
| `--holdings FILE` | JSON file `{ticker: dollar_value}` of current holdings, for `--run`. |
| `--output FILE` | Write a JSON/CSV report to this path. |
| `--cache-dir DIR` | Data cache directory (default: `.cache/market_data`). |
| `--refresh-cache` | Ignore cached data and re-download. |
| `-v`, `--verbose` | Enable debug logging. |

Run `python trading_strategy.py --help` at any time for the full, up-to-date list.

There is no test suite or linter configured yet — validate changes by running
`--test-history` (and `--list-universe` for quick sanity checks) and reading the printed
report.

## Architecture

- `trading_strategy.py` — CLI entry point; argument parsing and the two top-level commands
  (`--test-history`, `--run`) plus report printing.
- `strategy/universe.py` — static ticker universe and sector map.
- `strategy/data.py` — `yfinance`-backed price/fundamentals fetch with an on-disk parquet
  cache.
- `strategy/indicators.py` — plain price-based technical indicators (SMA, ROC, ATR,
  percentile rank), no external TA library.
- `strategy/signals.py` — the trend/fundamental/news scoring pillars and the composite score.
- `strategy/portfolio.py` — regime detection, position sizing/caps, and stop-loss/drawdown
  rules.
- `strategy/backtest.py` — the backtest engine.
- `strategy/live.py` — `--run` mode: computes today's regime/scores/target portfolio and a
  trade list to get there from given holdings. Deliberately has no order-execution code.

See [`CLAUDE.md`](CLAUDE.md) for more detail on the module layout, and
[`stock-trading-strategy.md`](stock-trading-strategy.md) for the design doc this code
implements.
