"""Static Nasdaq-100 tech/communication-services universe.

Limitation (see stock-trading-strategy.md §10): this is the *current* tech
subset of the index, used for the whole 50-quarter backtest window. A fully
point-in-time backtest would re-derive index membership as of each historical
quarter; free data sources do not provide that history, so a name that IPO'd
or joined the index partway through the window simply has no price data
before that date and is naturally excluded from selection until it does
(handled in `data.py` / `backtest.py`), rather than being back-filled or
assumed to exist earlier than it did.
"""

# ticker -> rough sub-sector bucket, used only for the sector concentration
# cap in portfolio.py. Not an official GICS classification.
UNIVERSE = {
    # Semiconductors
    "NVDA": "semiconductors",
    "AVGO": "semiconductors",
    "AMD": "semiconductors",
    "INTC": "semiconductors",
    "QCOM": "semiconductors",
    "TXN": "semiconductors",
    "AMAT": "semiconductors",
    "MU": "semiconductors",
    "ADI": "semiconductors",
    "LRCX": "semiconductors",
    "KLAC": "semiconductors",
    "MRVL": "semiconductors",
    "ASML": "semiconductors",
    "ON": "semiconductors",
    "MCHP": "semiconductors",
    "ARM": "semiconductors",
    # Software
    "MSFT": "software",
    "ADBE": "software",
    "CRM": "software",
    "INTU": "software",
    "SNPS": "software",
    "CDNS": "software",
    "PANW": "software",
    "CRWD": "software",
    "FTNT": "software",
    "ORCL": "software",
    "WDAY": "software",
    "DDOG": "software",
    "TEAM": "software",
    "ZS": "software",
    "MDB": "software",
    "APP": "software",
    "PLTR": "software",
    # Internet / communication services
    "GOOGL": "internet",
    "META": "internet",
    "NFLX": "internet",
    # Diversified hardware
    "AAPL": "hardware",
    "CSCO": "hardware",
    # E-commerce / payments
    "AMZN": "ecommerce",
    "PYPL": "ecommerce",
    "DASH": "ecommerce",
}

BENCHMARK = "QQQ"


def all_tickers() -> list[str]:
    return sorted(UNIVERSE.keys())


def sector_of(ticker: str) -> str:
    return UNIVERSE.get(ticker, "unknown")
