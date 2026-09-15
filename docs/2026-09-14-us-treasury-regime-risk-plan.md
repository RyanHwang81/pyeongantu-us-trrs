# US Treasury Regime Risk Dashboard Implementation Plan

> **For Hermes:** Execute this plan task-by-task with tests and fresh verification.

**Goal:** Build and publish a production, official-data US Treasury regime risk dashboard in the existing 평안투 Pages pattern.

**Architecture:** Deterministic Python collector/engine emits JSON embedded into a single responsive HTML. GitHub Actions on the existing Mac runner rebuilds and deploys Pages; WordPress KR/EN dashboard hubs link to a verified public page.

**Tech Stack:** Python stdlib, pandas, numpy, xlrd for official NY Fed `.xls`, unittest, native HTML/CSS/JS/SVG, GitHub Pages.

---

### Task 1: Freeze score configuration
- Create `config.json` with final/sub-score weights, risk bands, TP zones, calibration/event definitions.
- Test weights, risk ordering, and absence of frontend score constants.
- Verify failing test then pass.

### Task 2: Complete rate structure
- Add DGS2, DGS30, T10Y2Y, 10Y30Y, DFII10, T10YIE, expected-short component, FEDFUNDS, and official policy expectation when available.
- Preserve frequency and observation date in metadata.
- Add decomposition identity/unit tests.

### Task 3: Complete term-premium model and acceleration
- Use NY Fed ACM xls primary and FRED Kim-Wright alternative.
- Add 1M/3M/6M, 20/60-session slopes, rolling z-score, expanding percentile, model dispersion.
- Keep MOVE MISSING unless official/free source exists; label realized-vol proxy honestly.
- Calibrate acceleration override from expanding historical percentiles and configured bp floors.

### Task 4: Add official fiscal/supply layer
- Implement only verified Treasury/FRED/FiscalData/TIC/NY Fed endpoints.
- Separate deficit, debt, interest burden, borrowing/issuance/duration, foreign demand, dealer holdings.
- Keep unavailable series MISSING; do not let stale annual data masquerade as current monthly data.

### Task 5: Add auction stress
- Collect official Treasury auction fields for 2Y/5Y/7Y/10Y/20Y/30Y.
- Compute rolling 3/6 auction bid-to-cover, indirect/direct/dealer shares.
- Compute tail only if official when-issued data exists; otherwise MISSING.

### Task 6: Rebuild scoring and regimes
- Implement exact 30/40/30 final formula with all required sub-scores.
- Add configured score bands and acceleration minimum-band override.
- Add Growth, Inflation, Term Premium, Fiscal Supply, Fiscal Confidence regimes.
- Add stock+bond simultaneous weakness alert when data supports it.

### Task 7: Historical backtest
- Define multiple TP events without future leakage.
- Compute SPX/Nasdaq forward 1W/1M/3M/6M/12M returns, mean/median/win rate/max drawdown/n/25th/75th.
- Compare regime buckets; report nulls and small samples honestly.
- Add 2013, 2022, 2023, 2025, 2026 case views.

### Task 8: Public UI and docs
- Implement top cards, decomposition charts, TP zones/momentum, fiscal/supply/auction, matrix, transmission, cross-dashboard link.
- Add dark mode and 390px responsive rules.
- Finish DATA_SOURCES, METHODOLOGY, SCORING, BACKTEST, CHANGELOG.

### Task 9: Local verification
- Run full unittest, py_compile, live build, JSON finite/source/quality scan.
- Serve locally and verify desktop/mobile DOM, console, horizontal overflow, critical text/charts.
- Record screenshots and receipt.

### Task 10: Publish and integrate
- Create/push public `RyanHwang81/pyeongantu-us-trrs` repo.
- Enable Pages workflow, dispatch, verify Actions and public URL.
- Add SignalnFlow KR/EN dashboard hub cards and article/embed only after live Pages verification.
- Back up WordPress targets, run REST/public/browser read-back, and append durable operations note.
