"""Signal pillars from stock-trading-strategy.md §3, plus the composite score.

Note on the composite formula (§4 of the doc): the markdown's original
formula added a 0-1 news multiplier into a weighted sum of 0-100 scores,
which mixes units inconsistently. This implementation uses the intended
behavior instead — news is a *multiplier* on the trend+fundamental score,
not a third additive term — and the doc has been updated to match:

    composite = (trend_weight * trend_score + fundamental_weight * fundamental_score)
                * news_risk_multiplier

with trend_weight : fundamental_weight kept at the documented 40:35 ratio,
renormalized to sum to 1.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass

import pandas as pd

from . import indicators

logger = logging.getLogger(__name__)

TREND_WEIGHT = 0.40 / 0.75
FUNDAMENTAL_WEIGHT = 0.35 / 0.75

SEVERE_NEGATIVE_KEYWORDS = [
    "investigation", "subpoena", "fraud", "restate", "restatement",
    "withdraws guidance", "withdrew guidance", "guidance withdrawal",
    "accounting error", "resigns amid", "bankruptcy", "recall of",
    "class action", "sec charges", "doj charges",
]
NEGATIVE_KEYWORDS = [
    "misses estimates", "downgrade", "warns", "warning", "delay",
    "cuts guidance", "lowers guidance", "layoffs", "probe",
]


# --------------------------------------------------------------------------
# 3.1 Trend / technical score
# --------------------------------------------------------------------------

@dataclass
class TrendInputs:
    close: pd.DataFrame   # date x ticker
    high: pd.DataFrame
    low: pd.DataFrame


def compute_trend_scores(inputs: TrendInputs, as_of: pd.Timestamp) -> pd.Series:
    """Per-ticker trend score 0-100 as of a given date. NaN if insufficient history."""
    close = inputs.close
    if as_of not in close.index:
        return pd.Series(dtype=float)

    sma50 = indicators.sma(close, 50).loc[as_of]
    sma200 = indicators.sma(close, 200).loc[as_of]
    price = close.loc[as_of]
    roc = indicators.rate_of_change(close, 60).loc[as_of]  # ~12 trading weeks

    atr14 = pd.Series(
        {t: indicators.atr(inputs.high[t], inputs.low[t], close[t]).loc[as_of] for t in close.columns}
    )
    atr_pct = atr14 / price

    # 1) Primary trend structure — 25 pts
    trend_component = pd.Series(0.0, index=close.columns)
    trend_component[(price > sma50) & (sma50 > sma200)] = 25.0
    trend_component[(price > sma50) & (sma50 <= sma200) & (trend_component == 0)] = 15.0
    remaining = trend_component == 0
    trend_component[remaining & (price > sma200)] = 8.0

    # 2) Momentum — 25 pts, cross-sectional percentile of positive ROC
    roc_rank = indicators.percentile_rank(roc.where(roc > 0))
    momentum_component = (roc_rank * 25.0).fillna(0.0)

    # 3) Pullback quality — 25 pts, best when 0-8% above the 50-day SMA
    dist = (price - sma50) / sma50
    pullback_component = pd.Series(0.0, index=close.columns)
    in_band = (dist >= 0) & (dist <= 0.08)
    pullback_component[in_band] = 25.0
    below = dist < 0
    pullback_component[below] = (25.0 + dist[below] * 100.0).clip(lower=0.0)
    above = dist > 0.08
    pullback_component[above] = (25.0 - (dist[above] - 0.08) * 100.0).clip(lower=0.0)

    # 4) Volatility filter — 25 pts, lower relative ATR scores higher
    vol_rank = indicators.percentile_rank(atr_pct)
    vol_component = ((1.0 - vol_rank) * 25.0).fillna(0.0)

    score = trend_component + momentum_component + pullback_component + vol_component
    # Require at least a 200-day history to be scored at all (matches the
    # universe's natural IPO-recency handling described in universe.py).
    score[sma200.isna()] = float("nan")
    return score.rename("trend_score")


# --------------------------------------------------------------------------
# 3.2 Fundamental quality/value score
# --------------------------------------------------------------------------

def compute_fundamental_scores(
    fundamentals: dict[str, pd.DataFrame],
    shares: dict[str, pd.Series],
    close: pd.DataFrame,
    as_of: pd.Timestamp,
    tickers: list[str],
) -> pd.Series:
    """Per-ticker fundamental score 0-100 as of a date, using only fundamentals
    reported on or before `as_of` (point-in-time to the extent the source data
    allows — see data.py's module docstring for the depth limitation).
    Tickers with no usable fundamentals yet get NaN, which callers should
    treat as "neutral" (score 50), not as a penalty.
    """
    scores: dict[str, float] = {}
    revenue_growth: dict[str, float] = {}
    fcf_margin: dict[str, float] = {}
    roe: dict[str, float] = {}
    net_debt_to_ebitda: dict[str, float] = {}
    ps_percentile_input: dict[str, float] = {}

    for ticker in tickers:
        df = fundamentals.get(ticker)
        if df is None:
            continue
        known = df[df.index <= as_of]
        if len(known) < 5:  # need >=1 year-ago quarter plus the current one
            continue
        latest = known.iloc[-1]
        year_ago = known.iloc[-5]

        if pd.notna(latest["revenue"]) and pd.notna(year_ago["revenue"]) and year_ago["revenue"]:
            revenue_growth[ticker] = (latest["revenue"] - year_ago["revenue"]) / abs(year_ago["revenue"])

        ttm_revenue = known["revenue"].tail(4).sum()
        ttm_fcf = known["fcf"].tail(4).sum() if known["fcf"].notna().tail(4).all() else float("nan")
        if pd.notna(ttm_fcf) and ttm_revenue:
            fcf_margin[ticker] = ttm_fcf / ttm_revenue

        ttm_net_income = known["net_income"].tail(4).sum() if known["net_income"].notna().tail(4).all() else float("nan")
        if pd.notna(ttm_net_income) and pd.notna(latest["equity"]) and latest["equity"]:
            roe[ticker] = ttm_net_income / latest["equity"]

        ttm_ebitda = known["ebitda"].tail(4).sum() if known["ebitda"].notna().tail(4).all() else float("nan")
        if pd.notna(ttm_ebitda) and ttm_ebitda and pd.notna(latest["net_debt"]):
            net_debt_to_ebitda[ticker] = latest["net_debt"] / ttm_ebitda

        share_series = shares.get(ticker)
        if share_series is not None and ticker in close.columns and ttm_revenue:
            share_asof = share_series[share_series.index <= as_of]
            price_asof = close[ticker][close.index <= as_of]
            if not share_asof.empty and not price_asof.empty:
                market_cap = price_asof.iloc[-1] * share_asof.iloc[-1]
                ps_percentile_input[ticker] = market_cap / ttm_revenue

    growth_rank = indicators.percentile_rank(pd.Series(revenue_growth))
    fcf_rank = indicators.percentile_rank(pd.Series(fcf_margin))
    roe_rank = indicators.percentile_rank(pd.Series(roe))
    # lower net debt/EBITDA is better -> invert
    leverage_rank = indicators.percentile_rank(-pd.Series(net_debt_to_ebitda))
    # lower P/S relative to peers is better -> invert
    valuation_rank = indicators.percentile_rank(-pd.Series(ps_percentile_input))

    for ticker in tickers:
        if ticker not in revenue_growth and ticker not in fcf_margin:
            continue  # no data at all -> caller treats as neutral
        g = growth_rank.get(ticker, float("nan"))
        p = fcf_rank.get(ticker, float("nan"))
        r = roe_rank.get(ticker, float("nan"))
        lev = leverage_rank.get(ticker, float("nan"))
        val = valuation_rank.get(ticker, float("nan"))
        parts = [x for x in [g, p, r, lev, val] if pd.notna(x)]
        if not parts:
            continue
        scores[ticker] = 100.0 * sum(parts) / len(parts)

    return pd.Series(scores, name="fundamental_score")


# --------------------------------------------------------------------------
# 3.3 News / event risk overlay
# --------------------------------------------------------------------------

class NewsProvider(abc.ABC):
    @abc.abstractmethod
    def headlines(self, ticker: str) -> list[str]:
        """Recent headline strings for a ticker. Empty list if none/unavailable."""


class NullNewsProvider(NewsProvider):
    """No news data — always neutral. Used for the historical backtest, where
    no free source of point-in-time news exists for a 50-quarter window
    (see stock-trading-strategy.md §8's own sanity-check recommendation to
    compare with the overlay disabled)."""

    def headlines(self, ticker: str) -> list[str]:
        return []


class YFinanceNewsProvider(NewsProvider):
    """Live/current headlines only — used in `--run` mode, not in the backtest."""

    def headlines(self, ticker: str) -> list[str]:
        import yfinance as yf

        try:
            items = yf.Ticker(ticker).news or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("News fetch failed for %s: %s", ticker, exc)
            return []
        out = []
        for item in items:
            title = item.get("title") or item.get("content", {}).get("title")
            if title:
                out.append(title)
        return out


def classify_headline(headline: str) -> str:
    text = headline.lower()
    if any(kw in text for kw in SEVERE_NEGATIVE_KEYWORDS):
        return "severe_negative"
    if any(kw in text for kw in NEGATIVE_KEYWORDS):
        return "negative"
    return "neutral"


def news_risk_multiplier(provider: NewsProvider, ticker: str) -> tuple[float, str]:
    """Returns (multiplier, reason). multiplier: 1.0 normal, 0.5 negative, 0.0 severe."""
    worst = "neutral"
    for headline in provider.headlines(ticker):
        cls = classify_headline(headline)
        if cls == "severe_negative":
            return 0.0, f"severe negative headline: {headline!r}"
        if cls == "negative":
            worst = "negative"
    return (0.5, "negative headline pending review") if worst == "negative" else (1.0, "no adverse news")


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def composite_score(
    trend_score: pd.Series,
    fundamental_score: pd.Series,
    news_multipliers: pd.Series | None = None,
) -> pd.Series:
    """See module docstring for why this is multiplicative, not additive, in news."""
    fundamental_filled = fundamental_score.reindex(trend_score.index).fillna(50.0)
    base = TREND_WEIGHT * trend_score + FUNDAMENTAL_WEIGHT * fundamental_filled
    if news_multipliers is None:
        return base
    mult = news_multipliers.reindex(trend_score.index).fillna(1.0)
    return base * mult
