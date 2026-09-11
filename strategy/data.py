"""Market data access, backed by yfinance, with a simple on-disk cache.

All historical price/fundamentals data comes from Yahoo Finance via the
`yfinance` package. This is free, unauthenticated data with known gaps:

- Price history is reliable and deep (decades) for the tickers here.
- Quarterly fundamentals from `yfinance` typically only go back ~4-6 years
  for most names, not the full 50-quarter (12.5y) backtest window. The
  fundamental score (signals.py) degrades to "neutral" for any period where
  a ticker has no fundamentals data yet, rather than fabricating a value —
  see the limitation note printed in the backtest report.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(".cache/market_data")


def _cache_paths(cache_dir: Path, ticker: str) -> tuple[Path, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{ticker}_prices.parquet", cache_dir / f"{ticker}_fundamentals.parquet"


def fetch_price_history(
    tickers: list[str],
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Fetch full daily OHLCV history per ticker (auto-adjusted for splits/dividends).

    Returns {ticker: DataFrame[open, high, low, close, volume]}, indexed by
    date. Tickers that fail to download (delisted, typo, network error) are
    skipped with a warning rather than aborting the whole run.
    """
    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        price_path, _ = _cache_paths(cache_dir, ticker)
        if not refresh and price_path.exists():
            df = pd.read_parquet(price_path)
            out[ticker] = df
            continue
        try:
            raw = yf.Ticker(ticker).history(period="max", auto_adjust=True)
            if raw.empty:
                logger.warning("No price data returned for %s; skipping.", ticker)
                continue
            df = raw[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.to_parquet(price_path)
            out[ticker] = df
            time.sleep(0.15)  # be polite to the free endpoint
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not kill the run
            logger.warning("Failed to fetch price history for %s: %s", ticker, exc)
    return out


def _safe_row(df: pd.DataFrame | None, *names: str) -> pd.Series | None:
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def fetch_fundamentals(
    tickers: list[str],
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
) -> dict[str, pd.DataFrame]:
    """Best-effort quarterly fundamentals per ticker.

    Returns {ticker: DataFrame indexed by fiscal-quarter-end date with columns
    revenue, eps, fcf, equity, net_debt, ebitda, pe} — any column can be NaN
    if that field wasn't available from the data source. A ticker with no
    usable fundamentals data at all is simply omitted; callers must handle
    that (signals.py treats it as "neutral" score).
    """
    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        _, fund_path = _cache_paths(cache_dir, ticker)
        if not refresh and fund_path.exists():
            out[ticker] = pd.read_parquet(fund_path)
            continue
        try:
            df = _build_fundamentals_frame(ticker)
            if df is not None and not df.empty:
                df.to_parquet(fund_path)
                out[ticker] = df
            time.sleep(0.15)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch fundamentals for %s: %s", ticker, exc)
    return out


def fetch_shares_outstanding(
    tickers: list[str],
    cache_dir: Path = DEFAULT_CACHE_DIR,
    refresh: bool = False,
) -> dict[str, pd.Series]:
    """Historical shares-outstanding series per ticker, used only to turn
    quarterly revenue into a rough historical Price/Sales for the valuation
    sub-score. Best-effort: a ticker with no data is simply omitted, and the
    valuation sub-score falls back to neutral for it.
    """
    out: dict[str, pd.Series] = {}
    for ticker in tickers:
        path = cache_dir / f"{ticker}_shares.parquet"
        if not refresh and path.exists():
            out[ticker] = pd.read_parquet(path)["shares"]
            continue
        try:
            shares = yf.Ticker(ticker).get_shares_full(start="2000-01-01")
            if shares is None or shares.empty:
                continue
            shares.index = pd.to_datetime(shares.index).tz_localize(None)
            shares = shares.sort_index()
            shares.to_frame("shares").to_parquet(path)
            out[ticker] = shares
            time.sleep(0.15)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch shares outstanding for %s: %s", ticker, exc)
    return out


def _build_fundamentals_frame(ticker: str) -> pd.DataFrame | None:
    t = yf.Ticker(ticker)

    income = t.quarterly_financials
    balance = t.quarterly_balance_sheet
    cashflow = t.quarterly_cashflow

    revenue = _safe_row(income, "Total Revenue", "TotalRevenue")
    net_income = _safe_row(income, "Net Income", "NetIncome")
    ebit = _safe_row(income, "EBIT")
    d_and_a = _safe_row(cashflow, "Depreciation And Amortization", "Depreciation")
    op_cf = _safe_row(cashflow, "Operating Cash Flow", "Total Cash From Operating Activities")
    capex = _safe_row(cashflow, "Capital Expenditure", "CapitalExpenditures")
    total_equity = _safe_row(
        balance, "Common Stock Equity", "Stockholders Equity", "Total Stockholder Equity"
    )
    total_debt = _safe_row(balance, "Total Debt")
    cash = _safe_row(balance, "Cash And Cash Equivalents", "Cash")

    if revenue is None:
        return None  # no usable data at all for this ticker

    idx = revenue.index
    frame = pd.DataFrame(index=idx)
    frame["revenue"] = revenue
    frame["net_income"] = net_income.reindex(idx) if net_income is not None else pd.NA
    frame["fcf"] = (
        (op_cf.reindex(idx) + capex.reindex(idx))
        if op_cf is not None and capex is not None
        else pd.NA
    )
    frame["equity"] = total_equity.reindex(idx) if total_equity is not None else pd.NA
    ebitda = None
    if ebit is not None and d_and_a is not None:
        ebitda = ebit.reindex(idx) + d_and_a.reindex(idx)
    frame["ebitda"] = ebitda if ebitda is not None else pd.NA
    if total_debt is not None and cash is not None:
        frame["net_debt"] = total_debt.reindex(idx) - cash.reindex(idx)
    else:
        frame["net_debt"] = pd.NA

    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame.sort_index()
    frame = frame.apply(pd.to_numeric, errors="coerce")
    return frame
