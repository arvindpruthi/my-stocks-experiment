"""Live/paper signal generation for `--run` — the "check a few times a day" mode.

This does not place any trades. It computes today's regime, scores, and a
target portfolio, and prints/returns the trade list a human (or a broker
API wired in separately) would need to place to move from a given set of
current holdings to that target. Deliberately no order-execution code here —
connecting this to a real brokerage account is a separate, explicit decision.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import indicators, portfolio, signals, universe

logger = logging.getLogger(__name__)


@dataclass
class LiveSignals:
    as_of: pd.Timestamp
    regime: str
    target_weights: dict[str, float]
    scores: pd.DataFrame  # index ticker, columns trend/fundamental/news/composite
    excluded_for_news: dict[str, str] = field(default_factory=dict)


def generate_signals(
    prices: dict[str, pd.DataFrame],
    fundamentals: dict[str, pd.DataFrame],
    shares: dict[str, pd.Series],
    use_news: bool = True,
    use_fundamentals: bool = True,
) -> LiveSignals:
    tickers = [t for t in universe.all_tickers() if t in prices]
    bench = universe.BENCHMARK
    close = pd.DataFrame({t: prices[t]["close"] for t in tickers})
    high = pd.DataFrame({t: prices[t]["high"] for t in tickers})
    low = pd.DataFrame({t: prices[t]["low"] for t in tickers})
    as_of = close.dropna(how="all").index.max()

    qqq_close = prices[bench]["close"]
    current_regime = portfolio.regime(qqq_close, as_of)
    target_invested_pct, max_positions = portfolio.regime_targets(current_regime)

    trend_inputs = signals.TrendInputs(close=close, high=high, low=low)
    t_scores = signals.compute_trend_scores(trend_inputs, as_of).dropna()

    if use_fundamentals:
        f_scores = signals.compute_fundamental_scores(fundamentals, shares, close, as_of, list(t_scores.index))
    else:
        f_scores = pd.Series(dtype=float)

    news_provider = signals.YFinanceNewsProvider() if use_news else signals.NullNewsProvider()
    news_mult = {}
    excluded_for_news = {}
    for t in t_scores.index:
        mult, reason = signals.news_risk_multiplier(news_provider, t)
        news_mult[t] = mult
        if mult == 0.0:
            excluded_for_news[t] = reason
    news_series = pd.Series(news_mult)

    composite = signals.composite_score(t_scores, f_scores, news_series)

    eligible = [t for t in composite.index if news_mult.get(t, 1.0) > 0.0]
    if current_regime == "risk_off" and use_fundamentals and f_scores.notna().sum() >= 8:
        threshold = f_scores.quantile(0.75)
        eligible = [t for t in eligible if f_scores.get(t, 50.0) >= threshold]

    ranked = composite.reindex(eligible).dropna().sort_values(ascending=False)
    selected = ranked.head(max_positions).index.tolist()

    atr14 = pd.Series(
        {t: indicators.atr(high[t], low[t], close[t]).loc[as_of] for t in selected}
    )
    atr_pct = atr14 / close.loc[as_of, selected]
    weights = portfolio.size_positions(selected, atr_pct, target_invested_pct)

    score_table = pd.DataFrame(
        {
            "trend_score": t_scores,
            "fundamental_score": f_scores.reindex(t_scores.index),
            "news_multiplier": news_series.reindex(t_scores.index),
            "composite": composite,
        }
    ).sort_values("composite", ascending=False)

    return LiveSignals(
        as_of=as_of,
        regime=current_regime,
        target_weights=weights,
        scores=score_table,
        excluded_for_news=excluded_for_news,
    )


def print_report(result: LiveSignals, capital: float, current_holdings: dict[str, float] | None = None) -> None:
    print(f"\n=== Live signal check — {result.as_of.date()} ===")
    print(f"Regime: {result.regime}  |  target invested: {sum(result.target_weights.values()):.1%}")
    print("\nTop-ranked candidates:")
    print(result.scores.head(15).round(1).to_string())

    if result.excluded_for_news:
        print("\nExcluded by news kill-switch:")
        for t, reason in result.excluded_for_news.items():
            print(f"  {t}: {reason}")

    print("\nTarget portfolio:")
    for t, w in sorted(result.target_weights.items(), key=lambda kv: -kv[1]):
        print(f"  {t:6s} {w:6.1%}  (~${w * capital:,.0f})")

    if current_holdings:
        print("\nTrades needed vs. supplied current holdings (--holdings):")
        all_tickers = set(current_holdings) | set(result.target_weights)
        for t in sorted(all_tickers):
            current_val = current_holdings.get(t, 0.0)
            target_val = result.target_weights.get(t, 0.0) * capital
            delta = target_val - current_val
            if abs(delta) < 1.0:
                continue
            action = "BUY" if delta > 0 else "SELL"
            print(f"  {action:4s} {t:6s} ${abs(delta):,.0f}")


def write_report(result: LiveSignals, path: Path) -> None:
    payload = {
        "as_of": str(result.as_of.date()),
        "regime": result.regime,
        "target_weights": result.target_weights,
        "excluded_for_news": result.excluded_for_news,
        "scores": json.loads(result.scores.round(2).to_json(orient="index")),
    }
    path.write_text(json.dumps(payload, indent=2))
