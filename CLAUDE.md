# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

A Python implementation of the Nasdaq-100 tech quant trading strategy documented in
`stock-trading-strategy.md`. The strategy scores a static universe of Nasdaq-100
tech/communication-services names on trend, fundamentals, and news-risk pillars, builds a
risk-managed target portfolio, and can either backtest that logic or generate a live
(no-order-execution) signal report.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Market data (prices + fundamentals) comes from Yahoo Finance via `yfinance` and is cached
on disk under `.cache/market_data/` (gitignored) to avoid re-downloading on every run.

## Running

Everything goes through `trading_strategy.py`:

```bash
python trading_strategy.py --list-universe            # print the tracked tickers + benchmark
python trading_strategy.py --test-history              # 50-quarter backtest vs. buy-and-hold QQQ
python trading_strategy.py --test-history --quarters 20 --capital 25000
python trading_strategy.py --run                       # today's regime/scores/target portfolio (no trades placed)
python trading_strategy.py --run --holdings my_holdings.json --output today.json
```

Useful flags: `--rebalance {weekly,daily}`, `--no-fundamentals`, `--no-news`,
`--refresh-cache`, `-v/--verbose`. There is no test suite or linter configured yet — validate
changes by running `--test-history` (and `--list-universe` for quick sanity checks) and
reading the printed report.

## Architecture

- `trading_strategy.py` — CLI entry point; argument parsing and the two top-level commands
  (`--test-history`, `--run`) plus report printing.
- `strategy/universe.py` — static ticker universe and sector map (current membership, not
  point-in-time; see its module docstring for the tradeoff).
- `strategy/data.py` — `yfinance`-backed price/fundamentals fetch with an on-disk parquet
  cache; degrades gracefully when fundamentals aren't available far enough back.
- `strategy/indicators.py` — plain price-based technical indicators (SMA, ROC, ATR,
  percentile rank), no external TA library.
- `strategy/signals.py` — the trend/fundamental/news scoring pillars and the composite score
  (see its docstring for a documented deviation from the original doc's formula).
- `strategy/portfolio.py` — regime detection (risk-on/risk-off vs. 200-day QQQ SMA),
  position sizing/caps, and stop-loss/drawdown rules from the doc's §5-§6.
- `strategy/backtest.py` — the backtest engine (§7-§8 of the doc); documents in its own
  docstring where it simplifies vs. the full design (rebalance cadence, no historical news
  overlay, current-not-point-in-time universe, adjusted-close as a total-return proxy).
- `strategy/live.py` — `--run` mode: computes today's regime/scores/target portfolio and a
  trade list to get there from given holdings. Deliberately has no order-execution code.
- `stock-trading-strategy.md` — the design doc this code implements; consult it for the
  *why* behind thresholds and rules before changing them in code.

When changing strategy logic, keep `stock-trading-strategy.md` and the implementing module's
docstring in sync — several modules call out explicit, intentional deviations from the doc,
and new ones should be documented the same way rather than left implicit.
