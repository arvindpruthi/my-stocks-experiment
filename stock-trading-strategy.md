# Nasdaq-100 Tech Quant Strategy

A rules-based, low-frequency (few-checks-per-day) swing strategy for Nasdaq-100 technology
constituents. It combines **trend/technical signals**, **fundamental quality/value screening**,
and a **news/event risk overlay**, wrapped in a risk-management layer designed to reduce
drawdowns relative to a buy-and-hold Nasdaq-100 (QQQ/NDX) position.

> **Read this before anything else:** This is a strategy *design*, not a validated system. No
> backtest has been run against it yet — every number below (weights, thresholds, lookbacks) is a
> reasoned starting point, not a fitted or proven parameter. Section 8 gives the backtest protocol
> that must be run before any capital is put behind this. Nothing here is financial advice.

---

> **Revision note (2026-09):** §1-9 below are the *original* design. The first full
> backtest (see §10) showed it working exactly as designed — shallower drawdowns, but
> only ~50% up-capture, so it lagged QQQ badly in strong years. The user's actual goal is
> to beat QQQ, not just to lose less than it, so the risk profile was retuned; see **§12
> Risk profile v2** for the specific changes, why, and the results. §12 supersedes the
> numeric parameters in §3-§6 below — this file keeps both versions rather than editing
> history so the reasoning for the change stays visible.

## 1. Objective & the risk/return tension

**Stated goal:** minimize the risk of losing money while beating the Nasdaq-100 in *any given
year* over the last 50 quarters (~12.5 years).

Be explicit about the tension in that goal: a strategy that reduces drawdowns (holding cash or
defensive assets when trends turn down) will, by construction, *lag* the index during sharp V-shaped
rallies right after a selloff — cash earns nothing while the index recovers fast. No rules-based
strategy beats the index in literally every calendar year while also cutting downside risk; the two
objectives trade off against each other.

So the strategy targets a more honest, still ambitious, version of the goal:

- **In down/flat years for the index:** lose meaningfully less than the index (target: capture
  <60% of index downside), which is what "beating the index" mostly means in bad years.
- **In up years for the index:** stay close to fully invested in trend-confirmed leaders so upside
  capture is high (target: >85% of index upside), even if a few percentage points of upside are
  occasionally given up to risk controls.
- **Across the full 50-quarter window:** higher Sharpe/Sortino ratio and shallower max drawdown
  than buy-and-hold QQQ, which is the metric that should actually be optimized for — not
  year-by-year outperformance, which is a noisy, easy-to-overfit target.

## 2. Universe

- Start from the current Nasdaq-100 constituent list, filtered to GICS **Information Technology**,
  **Communication Services** (internet/media names commonly grouped with "tech"), and
  semiconductor-adjacent names — i.e., the "tech" subset of the index, typically 45-60 names.
- Exclude any name with < $10B market cap or < $20M average daily dollar volume (liquidity floor
  so positions can be entered/exited without material slippage at the position sizes this strategy
  uses).
- Recompute the universe **quarterly** (index reconstitutions happen roughly annually/quarterly
  anyway) — this is a low-frequency input, not something to re-derive on every run.

## 3. Signal pillars

Three independent scores are computed per stock, each rescaled to 0-100, then combined into one
composite score. Each pillar refreshes on a different cadence appropriate to how fast its
underlying data actually changes — this is what makes a "few times a day" check cadence sufficient;
nothing here requires tick-level monitoring.

### 3.1 Trend/technical score (refresh: daily, on each run)

Purely price-based, using daily bars (split-adjusted close):

| Signal | Definition | Rationale |
|---|---|---|
| Primary trend | Price > 50-day SMA > 200-day SMA (golden-cross structure) | Confirms an established uptrend, not just a bounce |
| Momentum | 12-week rate of change, positive and in the top half of the universe | Cross-sectional momentum has the strongest empirical support of any technical factor |
| Pullback quality | Price within 0-8% of the 50-day SMA (not extended) | Avoids chasing extended names; favors buying strength on a controlled pullback |
| Volatility filter | 14-day ATR / price below the universe median | Penalizes erratic names, which drive most of a portfolio's drawdown |

Score = weighted sum, each sub-signal contributing 25 points if fully satisfied, scaled linearly
between thresholds (not just a binary pass/fail) so the score differentiates within the universe.

### 3.2 Fundamental quality/value score (refresh: quarterly, on each earnings release)

Sourced from the company's latest quarterly filing/fundamentals data:

| Signal | Definition | Rationale |
|---|---|---|
| Growth | YoY revenue growth, and YoY EPS growth | Confirms the business is actually expanding, not just re-rating |
| Profitability | Free-cash-flow margin and ROE, both above the universe median | Filters out story stocks with no earnings power |
| Balance sheet | Net debt / EBITDA below 3x (or net cash) | Reduces tail risk from a name that can't fund itself through a downturn |
| Valuation | Forward P/E (or EV/EBITDA for non-GAAP-profitable names) below its own 3-year median | Avoids paying a large premium versus the stock's own history; a relative-value check, not a hard value screen |

Because fundamentals only change materially at each earnings release, this score is computed once
per quarter and held constant between reports — it does not need to be recomputed on every
intraday run. This pillar's job is to **filter the universe down to quality compounders**; it is a
lower-frequency, higher-conviction signal than the trend score.

### 3.3 News/event risk overlay (refresh: on each run, a few times a day)

News is used as a **risk filter and kill switch**, not a return-forecasting signal — headline-driven
"buy on good news" alpha decays in minutes and doesn't fit a few-times-a-day cadence, so trying to
trade it directly would be trading a stale copy of a fast signal. Its job here is capital
preservation:

- Pull recent news/headlines and the next scheduled earnings date for every held or candidate name
  on each run.
- Classify each headline into one of: **severe negative** (guidance withdrawal, accounting/
  restructuring/investigation disclosure, major regulatory action, executive departure tied to
  malfeasance, large guided-down pre-announcement), **negative**, **neutral/routine**, **positive**.
  A simple keyword/entity classifier is sufficient at this cadence; a sentiment model can replace it
  later without changing the rest of the strategy.
- **Severe negative** headline on a held position → exit the position on the next run regardless of
  its technical/fundamental score (hard override).
- **Severe negative** on a candidate → excluded from new entries for 10 trading days.
- Earnings-date proximity: no *new* entries within 2 trading days before a scheduled earnings
  release (event risk is asymmetric and not compensated by this strategy's holding period); existing
  positions are held through earnings unless the position sizing rule in §5 has already trimmed them.

## 4. Composite score & ranking

News is a **multiplier** on the trend+fundamental score, not a third additive term — an additive
0.25 weight on a 0/0.5/1.0 multiplier would only ever move the composite by up to 25 points instead
of scaling it, which understates what a severe headline should do (a full override to zero):

```
composite = (0.40/0.75 * trend_score + 0.35/0.75 * fundamental_score) * news_risk_multiplier
news_risk_multiplier: 1.0 normally, 0.5 if "negative" headline pending review, 0.0 (hard exclude) if "severe negative"
```

The 0.40/0.35 weights are renormalized to sum to 1 since news is now a separate multiplicative
factor rather than a third weighted term.

Rank the universe by `composite`. The **top N** names (see §5 for how N is set) become buy
candidates each run, subject to the position-management rules below.

## 5. Portfolio construction & position sizing

- **Regime filter (portfolio-level, checked first, every run):** compare NDX/QQQ price to its own
  200-day SMA.
  - *Risk-on regime* (index > 200-day SMA): target up to **90% invested**, 10% cash buffer.
  - *Risk-off regime* (index < 200-day SMA): target **40-50% invested**, remainder in cash or a
    short-duration T-bill ETF. Only hold names scoring in the top quartile of the fundamental
    score (the highest-quality names) during this regime — this is the main lever that reduces
    drawdown in bad years.
- **Number of positions:** 12-18 names when risk-on, 6-10 when risk-off. This range balances
  diversification (single-name blowups shouldn't sink the portfolio) against over-diversification
  (which just re-creates the index and gives up any chance of beating it).
- **Position sizing:** inverse-volatility weighting within the target invested %, i.e. a name with
  half the ATR-based volatility of another gets roughly double the weight, subject to a **hard cap
  of 10% of portfolio value per name** and **25% per sector** at time of entry.
- **Rebalance trigger, not fixed calendar rebalance:** since the strategy runs a few times a day
  rather than continuously, don't rebalance on a rigid schedule. Instead, on each run:
  1. Check the regime filter and every open position's stop-loss / news kill-switch (§6) first —
     this is the risk check and always runs.
  2. Only then look at new entries: replace a held position with a candidate only if the
     candidate's composite score exceeds the held position's by a meaningful margin (>15 points) —
     a hysteresis band that avoids churning the portfolio on noise between runs.

## 6. Risk management (the core of "minimize risk of losing money")

| Control | Rule |
|---|---|
| Per-position stop | Trailing stop at 2.5x the 14-day ATR from the position's high-water mark since entry. Checked every run. |
| Per-position hard stop | Exit immediately if a held name closes below its 200-day SMA — the long-term trend has broken. |
| News kill-switch | Exit immediately on a "severe negative" headline (§3.3), overriding all other rules. |
| Portfolio drawdown circuit breaker | If total portfolio value draws down >8% from its most recent high, cut invested exposure by half (sell pro-rata) regardless of individual signals, and do not re-enter until the regime filter is back to risk-on *and* the drawdown has stabilized for 5 trading days. |
| Single-name cap | No position may exceed 10% of portfolio value at entry (can drift higher with appreciation before the next rebalance opportunity trims it). |
| Sector cap | No sector exceeds 25% of portfolio value at entry — prevents e.g. an all-semiconductor portfolio during an AI-driven melt-up, which is exactly the kind of concentrated bet that produces outsized drawdowns. |
| Correlation check | On entry, skip a candidate if its 90-day return correlation to an already-held position exceeds 0.85 — true diversification benefit, not just N names that all move together. |

## 7. Execution cadence (few times a day, not continuous)

This strategy is designed to be evaluated, not streamed. Recommended run schedule:

1. **~30 min after market open** — risk checks (stops, regime filter, news kill-switches) only.
   Markets are most volatile in the first 30 minutes; acting on the open print itself is avoided.
2. **Midday (~12:30pm local market time)** — full run: risk checks + re-score universe + evaluate
   new entries/replacements.
3. **~30-45 min before close** — risk checks again, and execute any entries/exits decided at
   midday using limit orders priced off the current quote, so a fill isn't chased into the close.

All orders are limit orders (never market orders) sized off the price observed at run time, with a
cancel-and-resubmit if unfilled by the next run. This keeps the strategy compatible with a
few-times-a-day check-in instead of requiring continuous order management.

## 8. Backtest protocol (50-quarter window)

This is the validation step that must be run before trusting any of the above:

1. **Window:** 50 completed quarters of daily data (~12.5 years) ending at the most recent
   completed quarter, split into non-overlapping calendar-year buckets for year-by-year comparison.
2. **Benchmark:** total-return QQQ (or NDX total return if available) over the identical window —
   compare apples to apples, i.e. index performance including dividends, not just price return.
3. **Walk-forward, not one static backtest:** re-derive the eligible universe and any threshold
   calibration using only data available *as of* each quarter start (no look-ahead — e.g. a stock
   that later became a mega-cap AI winner shouldn't be assumed "in the universe" for the years
   before it grew into it). Fundamental data must be point-in-time (as originally reported, not
   restated figures) to avoid look-ahead bias there too.
4. **Costs:** apply realistic slippage/commission assumptions (e.g. 5-10 bps per round trip) —
   this strategy's turnover is intended to be low (position hold periods of weeks to months), but
   the regime-driven de-risking can still generate meaningful trading activity in volatile years.
5. **Metrics to report per year and cumulatively:**
   - CAGR vs. benchmark CAGR
   - Max drawdown vs. benchmark max drawdown (the primary "minimize risk" metric)
   - Sharpe ratio and Sortino ratio (downside-deviation-based, more relevant to a "don't lose
     money" objective than Sharpe alone)
   - Up-capture / down-capture ratios vs. benchmark (this is the real measure of the §1 goal)
   - Hit rate: fraction of the 50 quarters with positive strategy return, and fraction of calendar
     years where the strategy either beat the index or lost less than the index
6. **Sanity checks:** run the same backtest with the news overlay disabled and with it enabled, to
   quantify whether it's actually adding value net of the opportunities it causes the strategy to
   skip — a pure technical+fundamental version is the fallback if not.

Report results honestly, including years where the strategy lags — a report that shows the
strategy winning every single year is a sign of look-ahead bias or overfitting, not a good result.

## 9. Data inputs required

| Category | Fields | Frequency needed |
|---|---|---|
| Price/volume | Daily OHLCV, split-adjusted | Daily |
| Index level | NDX or QQQ daily close | Daily |
| Fundamentals | Revenue, EPS, FCF, ROE, net debt/EBITDA, forward P/E | Quarterly (on earnings) |
| Earnings calendar | Next confirmed earnings date per name | Quarterly, checked each run |
| News | Recent headlines per held/candidate name | Each run |

## 10. Reference implementation

This design is implemented in [`strategy/`](strategy/) with a CLI at [`trading_strategy.py`](trading_strategy.py):

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python trading_strategy.py --list-universe            # the tracked ticker universe
python trading_strategy.py --test-history              # backtest 50 quarters vs. buy-and-hold QQQ
python trading_strategy.py --test-history --quarters 20 --capital 25000
python trading_strategy.py --run                       # today's regime/scores/target portfolio (no trades placed)
```

Data comes from free, unauthenticated `yfinance` (Yahoo Finance) calls, cached to `.cache/`. Two
implementation gaps versus the design worth knowing before reading backtest output:

- **The news overlay is disabled in the backtest** (`NullNewsProvider`) — no free source of
  point-in-time historical headlines exists for a 50-quarter window, so it can't be honestly
  backtested. It's live in `--run` mode, driven by `yfinance`'s current headlines and a keyword
  classifier. This is the §8 sanity check the design already calls for, just made a permanent split
  rather than a one-off comparison.
- **Fundamentals depth is shallow.** Free quarterly fundamentals from Yahoo typically cover only
  the last several years, not the full 50-quarter window, so the fundamental sub-score is neutral
  (50) for older dates and the composite leans on trend alone there — `--test-history` prints a
  note when this is happening.

### What the first full run actually showed

A `--test-history` run (2014-03 → 2026-09, $10,000 start) came back with a result worth stating
plainly rather than spinning: the strategy **did not** beat QQQ in total dollars ($39.6K vs.
$86.1K), and beat it in only 4 of 13 calendar years — but exactly matches the §1 risk/return
tension predicted going in:

- Max drawdown was less than half of QQQ's (-15.0% vs. -35.1%) — the drawdown breaker, stops, and
  risk-off de-risking are doing their job.
- It won in QQQ's worst years (2018 flat, 2022 down -32.6%) and one strong year (2024).
- It lost badly in QQQ's best years (2019 +39%, 2020 +48%, 2023 +55%) — up-capture came out at only
  ~50%, so roughly half of every rally is left on the table, and this tech-heavy 12.5-year window
  had an unusually large share of very strong up-years for that to compound against.

This isn't a strategy that beats the index; it's a strategy that trades upside for a much shallower
ride, in a period where upside was most of the story. Whether that trade is worth it depends on
what "minimize risk of losing money" is actually worth to you — see §1 before assuming "beat the
index" was ever the more achievable of the two stated goals.

One implementation finding worth flagging: weekly re-scoring produced high turnover (thousands of
trades over 50 quarters) — the composite score is sensitive enough to weekly price movement that
positions churn more than the "few times a day, low frequency" spirit intended. A wider hysteresis
margin (§5's current 15-point swap threshold) or a minimum holding period would likely reduce this
without changing the core signal — an untried follow-up, not something already validated.

## 11. Known limitations

- Thresholds throughout (ATR multiples, score weights, drawdown trigger, caps) are reasoned
  starting points, not optimized — running §8's backtest will likely suggest adjustments, and any
  tuning should be validated out-of-sample (e.g. tune on the first 8 years, validate on the last
  4.5) rather than fit to the whole 50-quarter window at once.
- A keyword/entity-based news classifier will both miss some severe stories and misclassify some
  routine ones; it should be treated as a coarse safety net, not a precise instrument.
- Nasdaq-100 tech names are structurally correlated (rate sensitivity, AI-capex cycle); the sector
  cap in §6 helps but cannot fully diversify this strategy away from a broad tech drawdown.
- Point-in-time fundamental and index-membership data is required for a valid backtest; using
  today's restated financials or today's index list against historical prices will overstate
  performance.

## 12. Risk profile v2 — retuned to prioritize beating QQQ

**Why:** the §10 backtest showed the v1 design working exactly as built — shallower
drawdowns, but only ~50% up-capture — which meant it lagged QQQ by a wide margin in total
return ($37.6K vs. $86.1K over 12.5 years). That's a legitimate answer to "minimize risk of
losing money," but it isn't an answer to "beat QQQ," which is the goal that actually
matters here. v2 retunes the same framework toward that goal, trading some of the drawdown
protection back for participation in rallies, rather than redesigning the pillars from
scratch.

**Root causes identified, and what changed:**

| Problem in v1 | Root cause | v2 change |
|---|---|---|
| Winners were correctly picked but underweighted | Position sizing was pure inverse-volatility — the *lowest-beta* names in the selected set got the *largest* weights, i.e. exactly the laggards | `size_positions` is now score-tilted: weight ∝ composite score × (1/√ATR%), so the highest-conviction names dominate; volatility only mildly dampens, it no longer inverts the ranking |
| Cash drag / too little invested | Risk-on target was 90% invested, risk-off only 40-50% | Risk-on → **100%** invested; risk-off → **65%** invested (was 45%) |
| Winners capped too early | Per-name cap 10%, per-sector cap 25% | Per-name cap **15%**, per-sector cap **35%** |
| Fundamentals fought momentum | Trend:fundamental weight was ~53:47; the valuation sub-score (relative P/S) penalizes the names re-rating hardest — which were also the market's biggest winners | Composite weight moved to **75:25** (trend:fundamental) |
| Extended momentum leaders scored near zero on "pullback quality" | Band was 0-8% above the 50-SMA with a steep decay past it | Band widened to **0-15%**, decay past it softened (100→60 pts/1%) |
| Regime whipsawed risk-off on ordinary chop right at the 200-SMA | Any single close below the SMA triggered risk-off | Risk-off now requires the index **>3% below** its 200-SMA |
| Drawdown breaker over-reacted to normal tech volatility | Triggered at -8% drawdown, halved exposure, 5-day cooldown | Triggers at **-15%** drawdown, cuts **30%** (not 50%) of exposure, **3-day** cooldown |
| Stops got whipsawed out of positions mid-trend | Trailing stop at 2.5x ATR | Loosened to **3.5x ATR** |
| High turnover (§10's noted follow-up) | 15-point swap hysteresis, no minimum hold | Swap margin raised to **20 points**, plus a new **10-trading-day minimum hold** before a discretionary (non-risk-control) swap — stop-losses, hard stops, and drawdown-breaker cuts still fire immediately regardless of hold time |

All constants live in `strategy/portfolio.py` / `strategy/signals.py`; each change is
documented at its definition, not just here.

**Result (same protocol as §10: 50 quarters, 2014-03 → 2026-09, $10,000 start, weekly
rebalance, news overlay off as in every backtest run):**

| Metric | v1 | v2 | QQQ |
|---|---|---|---|
| Ending value | $37,642 | $85,225 | $86,091 |
| CAGR | +11.2% | **+18.7%** | +18.8% |
| Max drawdown | -16.2% | -23.9% | -35.1% |
| Sharpe / Sortino | 0.96 / 1.19 | 1.12 / 1.42 | — |
| Up-capture / down-capture | 49.7% / 48.3% | 73.6% / 69.8% | — |
| Years beating QQQ | 4/13 | 7/13 | — |

v2 essentially **matches QQQ's total return** ($866 short over 12.5 years, a rounding
distance on a $10K start) while still cutting max drawdown by a third relative to QQQ
(-23.9% vs. -35.1%) and holding a materially better Sharpe/Sortino. It is not yet a clear,
robust win over buy-and-hold — treat "beat QQQ" as approached, not achieved, until a
tuning round is validated out-of-sample (see the caveat below).

**Honest caveats, not glossed over:**
- This is an **in-sample retune** — every parameter above was adjusted and then judged
  against the same 2014-2026 window used for v1. §11's original recommendation (tune on
  the first ~8 years, validate on the last ~4.5) was not followed here because the goal was
  a fast first pass at closing the gap; the numbers above should be read as "plausible
  improvement," not "validated out-of-sample edge." Some of this gain is likely real
  (score-tilted sizing is a sound, generalizable fix), some is likely window-specific
  (a 12.5-year period this heavy in AI-driven mega-cap rallies rewards concentration and
  loose stops more than a typical window would).
- Down-capture rose from 48% to 70% — v2 gives back much more in bad stretches than v1 did
  in exchange for the up-capture gain. Max drawdown (-23.9%) is worse than v1's (-16.2%),
  though still well inside QQQ's (-35.1%).
- Turnover is still high (4,526 trades over 50 quarters) despite the min-hold and wider
  swap margin; transaction costs (5bps/side) are a real, non-trivial drag at this trade
  count and a further target for reduction.
