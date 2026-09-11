"""Portfolio construction and risk rules from stock-trading-strategy.md §5-§6."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import universe

NAME_CAP = 0.10
SECTOR_CAP = 0.25
CORRELATION_THRESHOLD = 0.85
DRAWDOWN_TRIGGER = 0.08
DRAWDOWN_COOLDOWN_DAYS = 5
STOP_ATR_MULTIPLE = 2.5
SCORE_SWAP_MARGIN = 15.0


def regime(qqq_close: pd.Series, as_of: pd.Timestamp, sma_window: int = 200) -> str:
    sma = qqq_close.rolling(sma_window, min_periods=sma_window).mean()
    if as_of not in qqq_close.index or pd.isna(sma.loc[as_of]):
        return "risk_on"  # default open while there isn't enough history to judge
    return "risk_on" if qqq_close.loc[as_of] >= sma.loc[as_of] else "risk_off"


def regime_targets(current_regime: str) -> tuple[float, int]:
    """Returns (target_invested_pct, max_positions)."""
    if current_regime == "risk_on":
        return 0.90, 15
    return 0.45, 8


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
    atr_pct: pd.Series,
    target_invested_pct: float,
    name_cap: float = NAME_CAP,
    sector_cap: float = SECTOR_CAP,
) -> dict[str, float]:
    """Inverse-volatility weights, water-filled against per-name and per-sector caps.

    Any exposure that can't be placed because of caps is left uninvested
    (falls back to cash) rather than silently exceeding a cap.
    """
    if not selected:
        return {}

    inv_vol = pd.Series({t: 1.0 / atr_pct.get(t, float("nan")) for t in selected}).dropna()
    inv_vol = inv_vol[inv_vol > 0]
    if inv_vol.empty:
        inv_vol = pd.Series(1.0, index=selected)  # equal-weight fallback

    weights = inv_vol / inv_vol.sum() * target_invested_pct
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
        """Returns True if the breaker trips on this update (i.e., halve exposure now)."""
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
