#!/usr/bin/env python3
"""CLI for the Nasdaq-100 tech quant strategy documented in stock-trading-strategy.md.

Examples:
    python trading_strategy.py --list-universe
    python trading_strategy.py --test-history
    python trading_strategy.py --test-history --quarters 20 --capital 25000
    python trading_strategy.py --run
    python trading_strategy.py --run --holdings my_holdings.json --output today.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from strategy import backtest, data, live, universe


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--test-history", action="store_true",
        help="Backtest the strategy over N quarters (default 50) vs. buy-and-hold QQQ.",
    )
    mode.add_argument(
        "--run", action="store_true",
        help="Generate today's regime/scores/target-portfolio (live, no trades placed).",
    )
    mode.add_argument("--list-universe", action="store_true", help="Print the tracked ticker universe and exit.")

    p.add_argument("--quarters", type=int, default=50, help="Backtest window length in quarters (default: 50).")
    p.add_argument("--capital", type=float, default=10_000, help="Starting capital in dollars (default: 10000).")
    p.add_argument(
        "--rebalance", choices=["weekly", "daily"], default="weekly",
        help="How often to fully re-score and rebalance in the backtest (default: weekly).",
    )
    p.add_argument("--no-fundamentals", action="store_true", help="Disable the fundamental score pillar.")
    p.add_argument("--no-news", action="store_true", help="Disable the live news overlay in --run mode.")
    p.add_argument("--holdings", type=Path, help="JSON file {ticker: dollar_value} of current holdings, for --run.")
    p.add_argument("--output", type=Path, help="Write a JSON/CSV report to this path.")
    p.add_argument("--cache-dir", type=Path, default=Path(".cache/market_data"), help="Data cache directory.")
    p.add_argument("--refresh-cache", action="store_true", help="Ignore cached data and re-download.")
    p.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    return p


def cmd_list_universe() -> None:
    for ticker in universe.all_tickers():
        print(f"{ticker:6s} {universe.sector_of(ticker)}")
    print(f"\n{len(universe.all_tickers())} tickers, benchmark {universe.BENCHMARK}")


def cmd_test_history(args: argparse.Namespace) -> None:
    tickers = universe.all_tickers() + [universe.BENCHMARK]
    print(f"Fetching price history for {len(tickers)} tickers (cache: {args.cache_dir})...")
    prices = data.fetch_price_history(tickers, cache_dir=args.cache_dir, refresh=args.refresh_cache)

    fundamentals, shares = {}, {}
    if not args.no_fundamentals:
        print("Fetching fundamentals (best-effort, degrades gracefully if unavailable)...")
        fundamentals = data.fetch_fundamentals(universe.all_tickers(), cache_dir=args.cache_dir, refresh=args.refresh_cache)
        shares = data.fetch_shares_outstanding(universe.all_tickers(), cache_dir=args.cache_dir, refresh=args.refresh_cache)

    print(f"Running {args.quarters}-quarter backtest (rebalance: {args.rebalance})...")
    result = backtest.run_backtest(
        prices, fundamentals, shares,
        quarters=args.quarters, capital=args.capital,
        rebalance_freq=args.rebalance, use_fundamentals=not args.no_fundamentals,
    )
    print_backtest_report(result, args.capital, args.quarters)

    if args.output:
        payload = {
            "quarters": args.quarters,
            "starting_capital": args.capital,
            "ending_value_strategy": float(result.equity_curve["strategy"].iloc[-1]),
            "ending_value_benchmark": float(result.equity_curve["benchmark"].iloc[-1]),
            "metrics": {k: (None if v != v else float(v)) for k, v in result.metrics.items()},
            "yearly_returns": json.loads(result.yearly_returns.to_json(orient="index", date_format="iso")),
            "trade_count": result.trade_count,
            "data_notes": result.data_notes,
        }
        args.output.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote report to {args.output}")


def print_backtest_report(result: backtest.BacktestResult, capital: float, quarters: int) -> None:
    strat_end = result.equity_curve["strategy"].iloc[-1]
    bench_end = result.equity_curve["benchmark"].iloc[-1]
    start_date = result.equity_curve.index[0].date()
    end_date = result.equity_curve.index[-1].date()
    m = result.metrics

    print(f"\n=== {quarters}-quarter backtest: {start_date} -> {end_date} ({m['years']:.1f} years) ===\n")
    print(f"  Starting capital:            ${capital:,.2f}")
    print(f"  Strategy ending value:       ${strat_end:,.2f}   (CAGR {m['strategy_cagr']:+.1%})")
    print(f"  Buy-and-hold QQQ ending val: ${bench_end:,.2f}   (CAGR {m['benchmark_cagr']:+.1%})")
    print(f"  Difference:                  ${strat_end - bench_end:,.2f}")
    print()
    print(f"  Strategy max drawdown:       {m['strategy_max_drawdown']:.1%}")
    print(f"  QQQ max drawdown:            {m['benchmark_max_drawdown']:.1%}")
    print(f"  Strategy Sharpe / Sortino:   {m['strategy_sharpe']:.2f} / {m['strategy_sortino']:.2f}")
    print(f"  Up-capture / down-capture:   {m['up_capture']:.1%} / {m['down_capture']:.1%}")
    print(f"  Total trades executed:       {result.trade_count}")

    print("\n  Year-by-year (calendar-year-end mark, first/last year partial):")
    yr = result.yearly_returns
    print(f"  {'Year':6s} {'Strategy':>10s} {'QQQ':>10s} {'Beat QQQ?':>10s}")
    for dt, row in yr.iterrows():
        beat = "yes" if row["strategy_beat_benchmark"] else "no"
        print(f"  {dt.year:<6d} {row['strategy_return']:>+9.1%} {row['benchmark_return']:>+9.1%} {beat:>10s}")

    beat_count = int(yr["strategy_beat_benchmark"].sum())
    print(f"\n  Beat QQQ in {beat_count}/{len(yr)} calendar years shown.")

    if result.data_notes:
        print("\n  Data limitations for this run:")
        for note in result.data_notes:
            print(f"   - {note}")
    print(
        "\n  See stock-trading-strategy.md §1 and §8 for why beating the index in "
        "every single year is not the actual design target — risk-adjusted "
        "return and shallower drawdowns are."
    )


def cmd_run(args: argparse.Namespace) -> None:
    tickers = universe.all_tickers() + [universe.BENCHMARK]
    print(f"Fetching latest price history for {len(tickers)} tickers...")
    prices = data.fetch_price_history(tickers, cache_dir=args.cache_dir, refresh=args.refresh_cache)

    fundamentals, shares = {}, {}
    if not args.no_fundamentals:
        fundamentals = data.fetch_fundamentals(universe.all_tickers(), cache_dir=args.cache_dir, refresh=args.refresh_cache)
        shares = data.fetch_shares_outstanding(universe.all_tickers(), cache_dir=args.cache_dir, refresh=args.refresh_cache)

    result = live.generate_signals(
        prices, fundamentals, shares,
        use_news=not args.no_news, use_fundamentals=not args.no_fundamentals,
    )

    current_holdings = None
    if args.holdings and args.holdings.exists():
        current_holdings = json.loads(args.holdings.read_text())

    live.print_report(result, args.capital, current_holdings)

    if args.output:
        live.write_report(result, args.output)
        print(f"\nWrote report to {args.output}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    if args.list_universe:
        cmd_list_universe()
    elif args.test_history:
        cmd_test_history(args)
    elif args.run:
        cmd_run(args)
    else:
        build_parser().print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
