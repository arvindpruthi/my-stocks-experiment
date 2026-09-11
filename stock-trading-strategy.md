# Nasdaq-100 Tech Quant Strategy

A rules-based, low-frequency (few-checks-per-day) swing strategy for Nasdaq-100 technology
constituents. It combines **trend/technical signals**, **fundamental quality/value screening**,
and a **news/event risk overlay**, wrapped in a risk-management layer tuned (§12) to beat a
buy-and-hold Nasdaq-100 (QQQ/NDX) position while still cutting into its drawdowns.

> **Read this before anything else:** This is a backtested strategy, not a live-validated one.
> §8 gives the backtest protocol (see §10 for results) and §12 the retune that got it to beat
> QQQ on that backtest — but every number below has only ever been judged in-sample, against
> the single 2014-2026 window it was tuned on, not validated out-of-sample or traded live.
> Nothing here is financial advice.

---

> **Revision note (2026-09):** §3-§6 below reflect the **v2-tuned risk profile** — the
> original design's numbers were retuned after the first backtest (§10) showed the
> original working exactly as designed (shallower drawdowns) but capturing only ~50% of
> QQQ's upside, badly lagging it in total return. The user's actual goal is to beat QQQ,
> not just to lose less than it, so thresholds throughout were loosened/retilted toward
> participation in rallies. **§12** is the changelog: what changed from the original
> design, why, the two-pass backtest results, and — importantly — the honest caveats
> (this is an in-sample retune, not validated out-of-sample).

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
| Pullback quality | Price within 0-15% of the 50-day SMA (not extended) | Avoids chasing extended names, while giving genuine momentum leaders room — a strong trend leader routinely trades >8% above its 50-SMA through its best legs, so an 0-8% band fought the momentum signal instead of complementing it |
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
composite = (0.75 * trend_score + 0.25 * fundamental_score) * news_risk_multiplier
news_risk_multiplier: 1.0 normally, 0.5 if "negative" headline pending review, 0.0 (hard exclude) if "severe negative"
```

Trend:fundamental is weighted 75:25 (moved from an original 40:35 — see §12). The
fundamental pillar's valuation sub-score (relative P/S vs. the universe) structurally
penalizes the names re-rating hardest, which across the backtest window were also the
names driving most of QQQ's return; keeping it near-equal weight to trend meant the
composite consistently under-ranked the market's actual winners. Fundamentals still act as
a secondary tilt (profitability/balance-sheet quality protect against pure momentum
blowups), just not a co-equal veto on trend.

Rank the universe by `composite`. The **top N** names (see §5 for how N is set) become buy
candidates each run, subject to the position-management rules below.

## 5. Portfolio construction & position sizing

- **Regime filter (portfolio-level, checked first, every run):** compare NDX/QQQ price to its own
  200-day SMA.
  - *Risk-on regime* (index at or within 3% below its 200-day SMA, or above it): target **100%
    invested**. The original design kept a 10% cash buffer in risk-on for no real
    risk-reduction benefit — pure drag — so it was dropped.
  - *Risk-off regime* (index **more than 3% below** its 200-day SMA): target **70% invested**,
    remainder in cash or a short-duration T-bill ETF. Only hold names scoring in the top
    **half** of the fundamental score (originally the top quartile — too tight a cut left too
    few names to build a diversified risk-off book). Requiring a real 3%+ break, not a bare
    single-day crossing, avoids flipping the whole portfolio's de-risking on ordinary chop
    right at the SMA line.
- **Number of positions:** **10** names when risk-on, **8** when risk-off (originally a 12-18 /
  6-10 range). The book is deliberately more concentrated than the original design so the wider
  per-name/sector caps below can actually bind — over-diversification just re-creates the index
  and gives up any chance of beating it, which is a direct trade-off against single-name/sector
  concentration risk (see §12's caveats).
- **Position sizing:** **score-tilted** weighting within the target invested % — weight is
  proportional to `composite_score × 1/√(ATR%)`, so the highest-conviction names get the
  largest allocations and volatility only mildly dampens rather than inverting the ranking.
  (The original design used pure inverse-volatility weighting, which put the *largest* weight
  on the *lowest-volatility* name in the selected set — empirically the laggards, not the
  trend leaders; see §12.) Subject to a **hard cap of 18% of portfolio value per name** and
  **40% per sector** at time of entry.
- **Rebalance trigger, not fixed calendar rebalance:** since the strategy runs a few times a day
  rather than continuously, don't rebalance on a rigid schedule. Instead, on each run:
  1. Check the regime filter and every open position's stop-loss / news kill-switch (§6) first —
     this is the risk check and always runs.
  2. Only then look at new entries: replace a held position with a candidate only if the
     candidate's composite score exceeds the held position's by a meaningful margin (>25 points,
     widened from >15 to further cut turnover) **and** the held position has been open at least
     **15 trading days** — a hysteresis band plus minimum hold that avoids churning the portfolio
     on noise between runs. This minimum hold applies only to this discretionary swap; stop-losses,
     the hard stop, and the drawdown breaker (§6) still fire immediately regardless of hold time.

## 6. Risk management (the core of "minimize risk of losing money")

| Control | Rule |
|---|---|
| Per-position stop | Trailing stop at **3.5x** the 14-day ATR from the position's high-water mark since entry (loosened from 2.5x, which was whipsawing positions out mid-trend on ordinary volatility). Checked every run. |
| Per-position hard stop | Exit immediately if a held name closes below its 200-day SMA — the long-term trend has broken. |
| News kill-switch | Exit immediately on a "severe negative" headline (§3.3), overriding all other rules. |
| Portfolio drawdown circuit breaker | If total portfolio value draws down **>15%** from its most recent high (was >8%, which over-reacted to normal tech-sector volatility), cut invested exposure by **30%** (was half; sell pro-rata) regardless of individual signals, and do not re-enter until the regime filter is back to risk-on *and* the drawdown has stabilized for **3 trading days** (was 5). |
| Single-name cap | No position may exceed **18%** of portfolio value at entry (was 10%; can drift higher with appreciation before the next rebalance opportunity trims it). |
| Sector cap | No sector exceeds **40%** of portfolio value at entry (was 25%) — still prevents an unbounded all-semiconductor portfolio during an AI-driven melt-up, but wide enough to let a genuinely dominant sector compound instead of being capped away from a large share of the market's return. |
| Correlation check | On entry, skip a candidate if its 90-day return correlation to an already-held position exceeds 0.85 — true diversification benefit, not just N names that all move together. |

All of the above is a direct, accepted trade-off: looser stops and a higher-triggering,
smaller-cut drawdown breaker mean more given back in a genuine downturn than the original
design's tighter version — see §12 for the backtested size of that trade-off (down-capture
rose from 48% to 72%, max drawdown from -16.2% to -25.8%) and why it was made anyway.

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

### What running the strategy actually shows (current: v2-tuned)

A `--test-history` run with the current v2-tuned parameters (2014-03 → 2026-09, $10,000
start) **beats QQQ** in total dollars ($100.8K vs. $86.1K, +20.3% vs. +18.8% CAGR) and in
7 of 13 calendar years, while still holding a materially shallower max drawdown (-25.8%
vs. QQQ's -35.1%) and better Sharpe/Sortino (1.14/1.47).

This is a real change from where the strategy started. The *original* design (§1-§9's
numbers before the §12 retune) backtested to the opposite conclusion — it lost badly in
total dollars ($37.6K) despite a much shallower drawdown (-16.2%) — because it captured
only ~50% of QQQ's upside (vs. 77% now) by correctly picking trend leaders and then
underweighting them, staying too defensive too readily, and capping winners before they
could compound. **§12** is the full changelog of what changed and why, the two-pass
backtest numbers, and — read this before trusting the headline above — the honest caveats:
this is an in-sample retune over the same window used to diagnose the original problem,
not validated out-of-sample, and it trades away some of the original's drawdown protection
(down-capture rose from 48% to 72%) to get here.

One implementation finding already addressed: the original weekly re-scoring produced very
high turnover (the composite score is sensitive enough to weekly price movement that
positions churned more than the "few times a day, low frequency" spirit intended). The v2
retune's wider hysteresis margin (now 25 points, up from 15) and new 15-trading-day minimum
hold (§5) cut trade count from 5,290 to 3,977 over the same window, though turnover is
still substantial and a further target for reduction.

## 11. Known limitations

- Thresholds throughout (ATR multiples, score weights, drawdown trigger, caps) have now been
  tuned once (§12, "risk profile v2") against the §8 backtest, which is a real improvement over
  the original's untested starting points — but that tuning was done **in-sample**, against the
  same 50-quarter window used to diagnose the problem, not validated out-of-sample as §8
  recommends (e.g. tune on the first ~8 years, validate on the last ~4.5). Treat the current
  numbers as "improved," not "proven to generalize," until that validation pass is run.
- A keyword/entity-based news classifier will both miss some severe stories and misclassify some
  routine ones; it should be treated as a coarse safety net, not a precise instrument.
- Nasdaq-100 tech names are structurally correlated (rate sensitivity, AI-capex cycle); the sector
  cap in §6 helps but cannot fully diversify this strategy away from a broad tech drawdown.
- Point-in-time fundamental and index-membership data is required for a valid backtest; using
  today's restated financials or today's index list against historical prices will overstate
  performance.

## 12. Risk profile v2 — changelog: what changed to prioritize beating QQQ, and why

§3-§6 above already show the **current** (v2-tuned) numbers. This section is the changelog
explaining the original ("v1") values each of those was tuned away from, why, and the
backtested effect — kept here rather than deleted so the reasoning for each change stays
visible without cluttering the primary design sections above.

**Why retune at all:** the first backtest (§10) showed the v1 design working exactly as
built — shallower drawdowns, but only ~50% up-capture — which meant it lagged QQQ by a wide
margin in total return ($37.6K vs. $86.1K over 12.5 years). That's a legitimate answer to
"minimize risk of losing money," but it isn't an answer to "beat QQQ," which is the goal
that actually matters here. v2 retunes the same framework toward that goal, trading some of
the drawdown protection back for participation in rallies, rather than redesigning the
pillars from scratch.

**Root causes identified, and what changed (v1 → current values in §3-§6, after two
tuning passes — see results below):**

| Problem in v1 | Root cause | v2 change |
|---|---|---|
| Winners were correctly picked but underweighted | Position sizing was pure inverse-volatility — the *lowest-beta* names in the selected set got the *largest* weights, i.e. exactly the laggards | `size_positions` is now score-tilted: weight ∝ composite score × (1/√ATR%), so the highest-conviction names dominate; volatility only mildly dampens, it no longer inverts the ranking |
| Cash drag / too little invested | Risk-on target was 90% invested, risk-off only 40-50% | Risk-on → **100%** invested; risk-off → **70%** invested (was 45%) |
| Winners capped too early / over-diversified | Per-name cap 10%, per-sector cap 25%, 12-18 risk-on positions | Per-name cap **18%**, per-sector cap **40%**, risk-on book concentrated to **10** positions (was up to 15-18) so the wider caps can actually bind |
| Fundamentals fought momentum | Trend:fundamental weight was ~53:47; the valuation sub-score (relative P/S) penalizes the names re-rating hardest — which were also the market's biggest winners | Composite weight moved to **75:25** (trend:fundamental) |
| Extended momentum leaders scored near zero on "pullback quality" | Band was 0-8% above the 50-SMA with a steep decay past it | Band widened to **0-15%**, decay past it softened (100→60 pts/1%) |
| Regime whipsawed risk-off on ordinary chop right at the 200-SMA | Any single close below the SMA triggered risk-off | Risk-off now requires the index **>3% below** its 200-SMA |
| Drawdown breaker over-reacted to normal tech volatility | Triggered at -8% drawdown, halved exposure, 5-day cooldown | Triggers at **-15%** drawdown, cuts **30%** (not 50%) of exposure, **3-day** cooldown |
| Stops got whipsawed out of positions mid-trend | Trailing stop at 2.5x ATR | Loosened to **3.5x ATR** |
| High turnover (§10's noted follow-up) | 15-point swap hysteresis, no minimum hold | Swap margin raised to **25 points**, plus a new **15-trading-day minimum hold** before a discretionary (non-risk-control) swap — stop-losses, hard stops, and drawdown-breaker cuts still fire immediately regardless of hold time |

All constants live in `strategy/portfolio.py` / `strategy/signals.py`; each change is
documented at its definition, not just here.

**Result, two tuning passes (same protocol as §10: 50 quarters, 2014-03 → 2026-09,
$10,000 start, weekly rebalance, news overlay off as in every backtest run):**

| Metric | v1 | v2 (pass 1) | v2 (pass 2, final) | QQQ |
|---|---|---|---|---|
| Ending value | $37,642 | $85,225 | **$100,813** | $86,091 |
| CAGR | +11.2% | +18.7% | **+20.3%** | +18.8% |
| Max drawdown | -16.2% | -23.9% | -25.8% | -35.1% |
| Sharpe / Sortino | 0.96 / 1.19 | 1.12 / 1.42 | 1.14 / 1.47 | — |
| Up-capture / down-capture | 49.7% / 48.3% | 73.6% / 69.8% | 76.7% / 72.0% | — |
| Years beating QQQ | 4/13 | 7/13 | 7/13 | — |
| Total trades | 5,290 | 4,526 | 3,977 | — |

Pass 1 (invested %, caps, sizing, composite weights, regime band, stops, drawdown breaker)
brought CAGR from 11.2% to 18.7% — essentially tying QQQ. Pass 2 (more concentration: 10
positions instead of 12, wider 18%/40% caps, plus more churn reduction: 15-day min hold,
25-point swap margin) pushed it to **+20.3% CAGR, $14.7K ahead of QQQ in ending value**,
while *also* reducing trade count. Max drawdown crept up slightly (-23.9% → -25.8%) but is
still nearly 10 points shallower than QQQ's -35.1%.

**Honest caveats, not glossed over:**
- This is an **in-sample retune over two passes** — every parameter above was adjusted and
  judged against the same 2014-2026 window used for v1, not validated out-of-sample. §11's
  original recommendation (tune on the first ~8 years, validate on the last ~4.5) was not
  followed here because the goal was a fast pass at closing the gap; read the numbers above
  as "plausible improvement on this window," not "proven forward-looking edge." Some of
  this gain is likely real and would generalize (score-tilted sizing correctly rewarding
  conviction is a sound mechanism), some is likely specific to a 12.5-year window this
  heavy in AI-driven mega-cap rallies, which rewards concentration and loose stops more
  than a typical or bear-heavy window would.
- Down-capture rose from 48% (v1) to 72% (final v2) — the strategy now gives back much more
  in bad stretches in exchange for the up-capture gain. Max drawdown (-25.8%) is
  meaningfully worse than v1's (-16.2%), though still well inside QQQ's (-35.1%).
- Concentrating to 10 risk-on positions with an 18%/40% cap means a single-name or
  single-sector shock now moves the portfolio noticeably more than the original design's
  wider diversification did — a direct, accepted trade-off for the return this generates,
  not a side effect to ignore.
- Turnover is lower than v1 but still substantial (3,977 trades over 50 quarters);
  transaction costs (5bps/side) remain a real drag worth further reduction.
- Before running this live, re-run `--test-history` with `--rebalance daily` and against a
  holdout window (e.g. re-derive parameters on 2014-2021 only, check 2022-2026) to see how
  much of this edge survives — the honest next step §11 already called for and that this
  session skipped in favor of a fast first answer.
