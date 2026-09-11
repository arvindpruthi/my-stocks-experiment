"""Portfolio construction and risk rules from stock-trading-strategy.md §5-§6.

Risk profile v2 (see stock-trading-strategy.md "Risk profile v2" section): the
original thresholds here were tuned to minimize drawdown and, per the doc's
§10 backtest note, that came at the cost of ~50% up-capture — the strategy
lagged QQQ badly in strong years. These constants and `size_positions` were
loosened/retilted specifically to close that gap: fuller invested %, wider
caps so winners can compound instead of being capped early, a
higher-conviction (score-tilted, not pure inverse-vol) sizing scheme, and a
drawdown breaker/stop set that reacts to real corrections rather than normal
tech-sector chop.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import universe

NAME_CAP = 0.15
SECTOR_CAP = 0.35
CORRELATION_THRESHOLD = 0.85
DRAWDOWN_TRIGGER = 0.15
DRAWDOWN_CUT_FRACTION = 0.30
DRAWDOWN_COOLDOWN_DAYS = 3
STOP_ATR_MULTIPLE = 3.5
SCORE_SWAP_MARGIN = 20.0
MIN_HOLDING_DAYS = 10
REGIME_RISK_OFF_BAND = 0.03  # index must be >3% below its 200-SMA to de-risk


def regime(qqq_close: pd.Series, as_of: pd.Timestamp, sma_window: int = 200) -> str:
    """Risk-off requires the index to be a meaningful `REGIME_RISK_OFF_BAND`
    below its 200-day SMA, not just a single close under the line. A bare
    crossing whipsaws the regime (and the de-risking it triggers) on ordinary
    chop right at the SMA; requiring a real break reduces that churn at the
    cost of a slightly later risk-off flip, which is an acceptable trade for
    a strategy now prioritizing up-capture."""
    sma = qqq_close.rolling(sma_window, min_periods=sma_window).mean()
    if as_of not in qqq_close.index or pd.isna(sma.loc[as_of]):
        return "risk_on"  # default open while there isn't enough history to judge
    return "risk_off" if qqq_close.loc[as_of] < sma.loc[as_of] * (1 - REGIME_RISK_OFF_BAND) else "risk_on"


def regime_targets(current_regime: str) -> tuple[float, int]:
    """Returns (target_invested_pct, max_positions).

    Risk-on now targets fully invested (was 90%, a 10% cash drag with no
    real risk-reduction benefit) with a more concentrated book (12 vs. 15
    names) so the wider name/sector caps below can actually matter. Risk-off
    still de-risks but to 65% (was 45%) — the old level gave up too much of
    the V-shaped recoveries that follow most tech corrections."""
    if current_regime == "risk_on":
        return 1.00, 12
    return 0.65, 8


def filter_correlated(
    ranked_candidates: list[str],
    already_selected: list[str],
    recent_returns: pd.DataFrame,
    threshold: float = CORRELATION_THRESHOLD,
) -> list[str]:
    """Drop candidates too correlated (90-day returns) with names already selected."""
    out = []
    selected = list(already_selected)
    for ticker in ranked_candidates:
        if ticker not in recent_returns.columns:
            out.append(ticker)
            continue
        too_correlated = False
        for held in selected:
            if held not in recent_returns.columns:
                continue
            pair = recent_returns[[ticker, held]].dropna()
            if len(pair) < 20:
                continue
            corr = pair[ticker].corr(pair[held])
            if pd.notna(corr) and corr > threshold:
                too_correlated = True
                break
        if not too_correlated:
            out.append(ticker)
            selected.append(ticker)
    return out


def size_positions(
    selected: list[str],
    scores: pd.Series,
    atr_pct: pd.Series,
    target_invested_pct: float,
    name_cap: float = NAME_CAP,
    sector_cap: float = SECTOR_CAP,
) -> dict[str, float]:
    """Score-tilted weights (composite score, mildly dampened by 1/sqrt(ATR%)),
    water-filled against per-name and per-sector caps.

    v2 change: pure inverse-volatility weighting (the original scheme) puts
    the *largest* weight on the lowest-volatility names in the selected set —
    which, empirically, means the laggards, not the trend leaders. That's a
    big part of why up-capture came out at ~50% in backtesting (see
    stock-trading-strategy.md): the strategy correctly picked winners but
    then underweighted them. This tilts sizing toward the highest-conviction
    (highest composite score) names instead, keeping only a square-root
    (rather than full) volatility dampener so a genuinely erratic name still
    gets sized down without capping the best-scoring, higher-beta winners
    that drive most of an index's return in a strong regime.

    Any exposure that can't be placed because of caps is left uninvested
    (falls back to cash) rather than silently exceeding a cap.
    """
    if not selected:
        return {}

    vol_dampener = pd.Series({t: 1.0 / (atr_pct.get(t, float("nan")) ** 0.5) for t in selected}).dropna()
    vol_dampener = vol_dampener[vol_dampener > 0]
    score_weight = scores.reindex(selected).clip(lower=1.0).fillna(1.0)

    raw = (score_weight * vol_dampener.reindex(selected)).dropna()
    if raw.empty or (raw <= 0).all():
        raw = score_weight  # fall back to score-only weighting if ATR data is missing
    raw = raw[raw > 0]
    if raw.empty:
        raw = pd.Series(1.0, index=selected)  # equal-weight fallback

    weights = raw / raw.sum() * target_invested_pct
    sector_totals: dict[str, float] = {}
    final: dict[str, float] = {}
    remaining_names = set(weights.index)

    # Iteratively cap and redistribute, a bounded number of passes.
    for _ in range(10):
        if not remaining_names:
            break
        capped_any = False
        for t in list(remaining_names):
            w = weights[t]
            sector = universe.sector_of(t)
            sector_room = sector_cap - sector_totals.get(sector, 0.0)
            capped_w = min(w, name_cap, max(sector_room, 0.0))
            if capped_w < w - 1e-9:
                final[t] = capped_w
                sector_totals[sector] = sector_totals.get(sector, 0.0) + capped_w
                remaining_names.discard(t)
                capped_any = True
        if not capped_any:
            break
        # redistribute the excess proportionally among the names still open
        placed = sum(final.values())
        excess = target_invested_pct - placed - sum(weights[t] for t in remaining_names)
        if remaining_names and excess > 1e-9:
            share = excess / len(remaining_names)
            for t in remaining_names:
                weights[t] = weights[t] + share

    for t in remaining_names:
        sector = universe.sector_of(t)
        sector_room = sector_cap - sector_totals.get(sector, 0.0)
        w = min(weights[t], name_cap, max(sector_room, 0.0))
        final[t] = w
        sector_totals[sector] = sector_totals.get(sector, 0.0) + w

    return final


@dataclass
class DrawdownBreaker:
    """Tracks the §6 portfolio-level drawdown circuit breaker."""

    peak_equity: float = float("-inf")
    tripped_on: pd.Timestamp | None = None

    def update(self, equity: float, as_of: pd.Timestamp) -> bool:
        """Returns True if the breaker trips on this update (i.e., cut exposure
        by DRAWDOWN_CUT_FRACTION now — see backtest.py)."""
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity <= 0:
            return False
        drawdown = 1.0 - equity / self.peak_equity
        if drawdown >= DRAWDOWN_TRIGGER and self.tripped_on is None:
            self.tripped_on = as_of
            return True
        return False

    def cooldown_active(self, as_of: pd.Timestamp) -> bool:
        if self.tripped_on is None:
            return False
        return (as_of - self.tripped_on).days < DRAWDOWN_COOLDOWN_DAYS

    def reset_if_recovered(self, current_regime: str, equity: float, as_of: pd.Timestamp) -> None:
        """Clear the trip only once the cooldown has passed, the index regime is
        risk-on, AND the portfolio's own drawdown has actually shrunk back to
        less than half the trigger — not just because time passed while the
        drawdown was still open. Without that last check, a choppy patch with
        QQQ nominally above its 200-SMA would re-trip and re-halve the same
        episode every rebalance instead of just once.
        """
        if self.tripped_on is None or self.cooldown_active(as_of) or current_regime != "risk_on":
            return
        if self.peak_equity <= 0:
            return
        drawdown = 1.0 - equity / self.peak_equity
        if drawdown < DRAWDOWN_TRIGGER / 2:
            self.tripped_on = None
