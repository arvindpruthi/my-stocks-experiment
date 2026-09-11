"""Walk-forward-ish backtest engine implementing stock-trading-strategy.md §7-§8.

Simplifications versus the full doc, made explicitly and called out in the
report output rather than silently:

- "A few times a day" in the doc becomes, at daily-bar resolution, a
  **weekly full re-score** (`--rebalance weekly`, the default) with **daily**
  stop-loss / drawdown-breaker checks — there is no finer signal than a daily
  close to react to at higher frequency without intraday data. `--rebalance
  daily` re-scores every trading day instead, at the cost of more churn.
- The news overlay (§3.3) is disabled in the backtest (`NullNewsProvider`) —
  no free source of point-in-time historical headlines exists for a
  50-quarter window. It is live in `--run` mode. This matches the doc's own
  §8 suggestion to sanity-check the strategy with the overlay on and off.
- Index membership and the sector map are the *current* ones applied
  throughout the window (see universe.py) — not a fully point-in-time
  reconstruction of Nasdaq-100 history.
- Adjusted close (yfinance `auto_adjust=True`) approximates total return
  (dividends + splits) for both the strategy and the QQQ benchmark.

Risk profile v2 (see stock-trading-strategy.md "Risk profile v2" section):
the drawdown breaker now cuts exposure by `portfolio.DRAWDOWN_CUT_FRACTION`
(30%) instead of always halving it, the risk-off fundamental-quality filter
uses the top half of the universe (was top quartile) so risk-off still holds
a workable number of names, and discretionary hysteresis swaps (not risk-control
exits — those still fire immediately) respect `portfolio.MIN_HOLDING_DAYS` to
cut churn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from . import indicators, portfolio, signals, universe

logger = logging.getLogger(__name__)

TX_COST_BPS = 0.0005  # 5 bps per side


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame          # columns: strategy, benchmark
    yearly_returns: pd.DataFrame
    metrics: dict
    trade_count: int
    data_notes: list[str] = field(default_factory=list)


def _build_panels(prices: dict[str, pd.DataFrame], tickers: list[str]) -> dict[str, pd.DataFrame]:
    close = pd.DataFrame({t: prices[t]["close"] for t in tickers if t in prices})
    high = pd.DataFrame({t: prices[t]["high"] for t in tickers if t in prices})
    low = pd.DataFrame({t: prices[t]["low"] for t in tickers if t in prices})
    return {"close": close, "high": high, "low": low}


def run_backtest(
    prices: dict[str, pd.DataFrame],
    fundamentals: dict[str, pd.DataFrame],
    shares: dict[str, pd.Series],
    quarters: int,
    capital: float,
    rebalance_freq: str = "weekly",
    use_fundamentals: bool = True,
) -> BacktestResult:
    tickers = [t for t in universe.all_tickers() if t in prices]
    bench = universe.BENCHMARK
    if bench not in prices:
        raise ValueError(f"Benchmark {bench} price data unavailable — cannot backtest.")

    panels = _build_panels(prices, tickers)
    close = panels["close"]
    high = panels["high"]
    low = panels["low"]

    qqq_close_full = prices[bench]["close"]
    end_date = min(close.dropna(how="all").index.max(), qqq_close_full.index.max())
    start_date = end_date - relativedelta(months=3 * quarters)
    warmup_start = start_date - relativedelta(days=420)  # room for 200-day SMA to warm up

    calendar = qqq_close_full[(qqq_close_full.index >= warmup_start) & (qqq_close_full.index <= end_date)].index
    close = close.reindex(calendar)
    high = high.reindex(calendar)
    low = low.reindex(calendar)
    qqq_close = qqq_close_full.reindex(calendar)

    trading_days = calendar[calendar >= start_date]
    if trading_days.empty:
        raise ValueError("No trading days in the requested window — check data availability.")
    actual_start = trading_days[0]

    logger.info("Backtest window: %s -> %s (%d trading days)", actual_start.date(), end_date.date(), len(trading_days))

    # Precompute panels used every day (performance-critical).
    sma200 = indicators.sma(close, 200)
    atr14 = pd.DataFrame({t: indicators.atr(high[t], low[t], close[t]) for t in close.columns})
    trend_inputs = signals.TrendInputs(close=close, high=high, low=low)

    if rebalance_freq == "daily":
        rebalance_dates = set(trading_days)
    else:
        rebalance_dates = {d for d in trading_days if d.weekday() == 0}
    rebalance_dates.add(actual_start)

    data_notes = []
    if use_fundamentals:
        tickers_with_fundamentals = [t for t in tickers if t in fundamentals]
        if len(tickers_with_fundamentals) < len(tickers) * 0.5:
            data_notes.append(
                f"Only {len(tickers_with_fundamentals)}/{len(tickers)} tickers had usable fundamentals "
                "data at all; free quarterly-fundamentals history is typically much shorter than "
                "50 quarters, so the fundamental sub-score is neutral (50) for most of the window "
                "and the composite score leans heavily on trend for older dates."
            )

    cash = capital
    holdings: dict[str, float] = {}       # ticker -> shares
    high_water: dict[str, float] = {}     # ticker -> price high-water mark since entry
    entry_date: dict[str, pd.Timestamp] = {}  # ticker -> date position was opened
    breaker = portfolio.DrawdownBreaker()
    trade_count = 0

    strategy_values = []
    dates_out = []

    for d in trading_days:
        prices_today = close.loc[d]

        # 1) mark-to-market
        equity = cash + sum(holdings.get(t, 0.0) * prices_today.get(t, 0.0) for t in holdings)

        # 2) drawdown circuit breaker
        current_regime = portfolio.regime(qqq_close, d)
        if breaker.update(equity, d):
            for t in list(holdings.keys()):
                px = prices_today.get(t)
                if px is None or pd.isna(px):
                    continue
                sell_shares = holdings[t] * portfolio.DRAWDOWN_CUT_FRACTION
                proceeds = sell_shares * px
                cash += proceeds * (1 - TX_COST_BPS)
                holdings[t] -= sell_shares
                trade_count += 1
            logger.info(
                "%s: drawdown breaker tripped, exposure cut %.0f%%.",
                d.date(), portfolio.DRAWDOWN_CUT_FRACTION * 100,
            )
        breaker.reset_if_recovered(current_regime, equity, d)

        # 3) per-position stop checks
        for t in list(holdings.keys()):
            px = prices_today.get(t)
            if px is None or pd.isna(px):
                continue
            high_water[t] = max(high_water.get(t, px), px)
            atr_t = atr14.at[d, t] if t in atr14.columns and d in atr14.index else float("nan")
            sma200_t = sma200.at[d, t] if t in sma200.columns and d in sma200.index else float("nan")
            stop_level = high_water[t] - portfolio.STOP_ATR_MULTIPLE * atr_t if pd.notna(atr_t) else None
            hit_trailing_stop = stop_level is not None and px < stop_level
            hit_hard_stop = pd.notna(sma200_t) and px < sma200_t
            if hit_trailing_stop or hit_hard_stop:
                cash += holdings[t] * px * (1 - TX_COST_BPS)
                del holdings[t]
                high_water.pop(t, None)
                entry_date.pop(t, None)
                trade_count += 1

        # 4) rebalance (full re-score + position changes)
        if d in rebalance_dates and not breaker.cooldown_active(d) and not (
            breaker.tripped_on is not None and current_regime != "risk_on"
        ):
            target_invested_pct, max_positions = portfolio.regime_targets(current_regime)

            t_scores = signals.compute_trend_scores(trend_inputs, d).dropna()
            if use_fundamentals:
                f_scores = signals.compute_fundamental_scores(fundamentals, shares, close, d, list(t_scores.index))
            else:
                f_scores = pd.Series(dtype=float)
            composite = signals.composite_score(t_scores, f_scores)

            eligible = list(t_scores.index)
            if current_regime == "risk_off" and use_fundamentals and f_scores.notna().sum() >= 8:
                # Top half (was top quartile) — still tilts risk-off holdings
                # toward quality, but a quartile cut left too few names to
                # build a diversified book, forcing concentration that then
                # got clipped hard by the per-name cap.
                threshold = f_scores.quantile(0.5)
                eligible = [t for t in eligible if f_scores.get(t, 50.0) >= threshold]

            ranked = composite.reindex(eligible).dropna().sort_values(ascending=False).index.tolist()

            held = [t for t in holdings.keys() if t in ranked]
            dropped = [t for t in holdings.keys() if t not in ranked]
            for t in dropped:  # no longer eligible at all -> exit
                px = prices_today.get(t)
                if px is not None and pd.notna(px):
                    cash += holdings[t] * px * (1 - TX_COST_BPS)
                trade_count += 1
                del holdings[t]
                high_water.pop(t, None)
                entry_date.pop(t, None)

            candidates = [t for t in ranked if t not in held]
            returns_panel = close.pct_change().loc[:d].tail(90)
            candidates = portfolio.filter_correlated(candidates, held, returns_panel)

            target = list(held)
            open_slots = max_positions - len(target)
            target.extend(candidates[:max(open_slots, 0)])

            # hysteresis swap: replace weakest held name if a leftover candidate
            # clearly beats it. v2: only swap out held names that have cleared
            # MIN_HOLDING_DAYS — this is a discretionary "found something
            # better" trade, not a risk control, so it shouldn't undo a
            # position before it's had time to work.
            leftover = [c for c in candidates if c not in target]
            swaps = 0
            while leftover and target and swaps < 3:
                swappable = [
                    t for t in target
                    if t not in entry_date or (d - entry_date[t]).days >= portfolio.MIN_HOLDING_DAYS
                ]
                if not swappable:
                    break
                held_scores = composite.reindex(swappable).sort_values()
                worst_held, worst_score = held_scores.index[0], held_scores.iloc[0]
                best_candidate, best_score = leftover[0], composite.get(leftover[0], float("-inf"))
                if best_score - worst_score > portfolio.SCORE_SWAP_MARGIN:
                    target.remove(worst_held)
                    target.append(best_candidate)
                    leftover.pop(0)
                    swaps += 1
                else:
                    break

            atr_pct = (atr14.loc[d] / prices_today).reindex(target)
            weights = portfolio.size_positions(target, composite.reindex(target), atr_pct, target_invested_pct)

            for t in list(holdings.keys()):
                if t not in weights:
                    px = prices_today.get(t)
                    if px is not None and pd.notna(px):
                        cash += holdings[t] * px * (1 - TX_COST_BPS)
                    trade_count += 1
                    del holdings[t]
                    high_water.pop(t, None)
                    entry_date.pop(t, None)

            equity_now = cash + sum(holdings.get(t, 0.0) * prices_today.get(t, 0.0) for t in holdings)
            for t, w in weights.items():
                px = prices_today.get(t)
                if px is None or pd.isna(px) or px <= 0:
                    continue
                target_value = w * equity_now
                target_shares = target_value / px
                delta_shares = target_shares - holdings.get(t, 0.0)
                # Rebalance band: skip trades that are just noise from weekly
                # target-weight drift, not a real position change — a more
                # realistic (and much lower-turnover) approximation of "few
                # times a day" than trading every $1 of drift.
                if abs(delta_shares * px) < max(50.0, 0.005 * equity_now):
                    continue
                notional = delta_shares * px
                cash -= notional + abs(notional) * TX_COST_BPS
                holdings[t] = holdings.get(t, 0.0) + delta_shares
                high_water.setdefault(t, px)
                entry_date.setdefault(t, d)
                trade_count += 1

        equity_end_of_day = cash + sum(holdings.get(t, 0.0) * prices_today.get(t, 0.0) for t in holdings)
        strategy_values.append(equity_end_of_day)
        dates_out.append(d)

    qqq_window = qqq_close.reindex(dates_out)
    qqq_shares = capital / qqq_window.iloc[0]
    benchmark_values = qqq_window * qqq_shares

    equity_curve = pd.DataFrame(
        {"strategy": strategy_values, "benchmark": benchmark_values.values}, index=dates_out
    )

    yearly = _yearly_returns(equity_curve)
    metrics = _compute_metrics(equity_curve)

    return BacktestResult(
        equity_curve=equity_curve,
        yearly_returns=yearly,
        metrics=metrics,
        trade_count=trade_count,
        data_notes=data_notes,
    )


def _yearly_returns(equity_curve: pd.DataFrame) -> pd.DataFrame:
    # Year-over-year change of year-end equity values, with the first year
    # measured from the actual start of the series (not Jan 1).
    yearly = equity_curve.resample("YE").last()
    out = pd.DataFrame(index=yearly.index, columns=["strategy_return", "benchmark_return"], dtype=float)
    prev_strategy = equity_curve["strategy"].iloc[0]
    prev_benchmark = equity_curve["benchmark"].iloc[0]
    for dt in yearly.index:
        s_end = yearly.at[dt, "strategy"]
        b_end = yearly.at[dt, "benchmark"]
        out.at[dt, "strategy_return"] = s_end / prev_strategy - 1
        out.at[dt, "benchmark_return"] = b_end / prev_benchmark - 1
        prev_strategy, prev_benchmark = s_end, b_end
    out["strategy_beat_benchmark"] = out["strategy_return"] > out["benchmark_return"]
    return out


def _max_drawdown(series: pd.Series) -> float:
    running_max = series.cummax()
    drawdown = series / running_max - 1.0
    return drawdown.min()


def _compute_metrics(equity_curve: pd.DataFrame) -> dict:
    strat_returns = equity_curve["strategy"].pct_change().dropna()
    bench_returns = equity_curve["benchmark"].pct_change().dropna()

    years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
    strat_cagr = (equity_curve["strategy"].iloc[-1] / equity_curve["strategy"].iloc[0]) ** (1 / years) - 1
    bench_cagr = (equity_curve["benchmark"].iloc[-1] / equity_curve["benchmark"].iloc[0]) ** (1 / years) - 1

    strat_vol = strat_returns.std() * np.sqrt(252)
    downside = strat_returns[strat_returns < 0]
    strat_sortino_vol = downside.std() * np.sqrt(252) if len(downside) else float("nan")

    up_days = bench_returns > 0
    down_days = bench_returns < 0
    up_capture = (
        strat_returns[up_days].mean() / bench_returns[up_days].mean()
        if up_days.any() and bench_returns[up_days].mean() != 0 else float("nan")
    )
    down_capture = (
        strat_returns[down_days].mean() / bench_returns[down_days].mean()
        if down_days.any() and bench_returns[down_days].mean() != 0 else float("nan")
    )

    return {
        "strategy_cagr": strat_cagr,
        "benchmark_cagr": bench_cagr,
        "strategy_max_drawdown": _max_drawdown(equity_curve["strategy"]),
        "benchmark_max_drawdown": _max_drawdown(equity_curve["benchmark"]),
        "strategy_sharpe": (strat_returns.mean() * 252) / strat_vol if strat_vol else float("nan"),
        "strategy_sortino": (strat_returns.mean() * 252) / strat_sortino_vol if strat_sortino_vol else float("nan"),
        "up_capture": up_capture,
        "down_capture": down_capture,
        "years": years,
    }
