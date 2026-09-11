"""Plain price-based technical indicators, no external TA library required."""

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def rate_of_change(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range (Wilder's smoothing via simple rolling mean)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def percentile_rank(values: pd.Series) -> pd.Series:
    """Rank each value 0..1 among the non-NaN values in the series."""
    valid = values.dropna()
    if len(valid) < 2:
        return pd.Series(np.nan, index=values.index)
    ranks = valid.rank(pct=True)
    return ranks.reindex(values.index)
