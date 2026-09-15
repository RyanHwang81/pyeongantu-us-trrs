# US Treasury Regime Risk Dashboard — Design

## Goal

평안투 기존 Pages 대시보드 패턴을 재사용해 미국 장기금리 상승을 `정책 기대 / 실질금리 / 기대인플레 / 텀프리미엄 / 재정·발행 / 입찰수요 / 시장 전염`으로 분해하고, TRRS 0–100과 세 개의 하위 점수를 제공한다.

## Existing architecture reused

- standalone public GitHub Pages repository
- Python stdlib + pandas/numpy deterministic builder
- official-source server-side collection with timeout/retry/cache
- embedded JSON single-file HTML, no runtime API key
- scheduled owner Mac self-hosted GitHub Actions runner
- SignalnFlow DESIGN.md warm paper/ink/red visual tokens
- iframe `gl-height` message contract
- unittest-based engine contract

No framework rewrite, SPA, database, paid feed, or new chart library.

## Files

- `engine.py`: definitions, normalization, level/momentum/acceleration, sub-scores, override, regime classifier, event/backtest
- `build.py`: official collectors, validation/quality metadata, payload, deterministic narrative
- `config.json`: weights, bands, calibration thresholds, event definitions
- `template.html`: public dashboard, native SVG charts, matrix, cases, methodology cards
- `tests/`: source IDs, units/bp, scores, override, missing/stale, backtest, HTML contract
- `docs/`: DATA_SOURCES, METHODOLOGY, SCORING, BACKTEST, CHANGELOG
- `.github/workflows/update.yml`: schedule/build/deploy

## Score architecture

- Rate Stress 30%
- Term Premium Stress 40%
- Fiscal Confidence Stress 30%
- Final TRRS = 0.30R + 0.40TP + 0.30F
- market transmission is context/alert, not a fourth weighted sub-score
- inputs are normalized using historical percentile plus documented economic zones when available
- acceleration override sets a minimum risk band only after expanding-history calibration; threshold defaults remain configuration, not frontend constants
- missing required sub-score blocks final TRRS rather than silently reweighting across absent structural layers

## Data integrity

- official US government/Federal Reserve sources are canonical
- term premium is model-estimated; ACM primary, Kim-Wright alternative, range and dispersion visible
- SF Fed model appears only when a stable official series is available
- MOVE, forward P/E, when-issued yield, auction tail remain missing unless an official/free stable feed is available; no proxy mislabeled as the original
- latest good observation may remain visible with STALE; no interpolation presented as a new observation
- metadata per KPI includes source URL, series ID, definition, unit, frequency, latest/previous/update time, historical range, quality, notes

## UI

1. 5-second hero: TRRS, three sub-scores, current regime, confidence/as-of
2. top eight KPI cards
3. two yield decompositions
4. term-premium history with configured risk bands and momentum/acceleration
5. fiscal, issuance, auction, transmission layers
6. 2×2 regime matrix
7. historical event backtest and selectable case studies
8. Cross-Dashboard Risk link to AI CapEx dashboard; no score mixing
9. mobile-first warm paper UI; dark-mode palette via `prefers-color-scheme`

## Verification

- deterministic unit tests including 1%=100bp
- build with live official data
- JSON schema/finite-number/source checks
- static HTML contract and no internal/secret leakage
- browser DOM/console/390px and desktop QA
- local HTTP smoke, then GitHub Pages deploy and public URL read-back
- SignalnFlow KR/EN dashboard hub cards only after Pages is verified
