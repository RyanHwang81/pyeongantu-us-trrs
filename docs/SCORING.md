# SCORING

`config.json` is the only score configuration source. Frontend does not contain the final formula.

## Final

```
TRRS = 0.30 × Rate Stress
     + 0.40 × Term Premium Stress
     + 0.30 × Fiscal Confidence Stress
```

All three sub-scores must be available. Market transmission is context/alert only.

## Rate Stress
- 10Y level 20%
- 10Y real yield 35%
- 10Y momentum 20%
- curve structure 10%
- Fed expectation proxy 15%

## Term Premium Stress
- ACM TP level 35%
- 3M TP change 25%
- TP acceleration 15%
- rate-vol proxy 15% (labeled as proxy, not MOVE)
- model dispersion 10%

## Fiscal Confidence Stress
- deficit/GDP 20%
- interest burden 20%
- issuance pressure 25%
- auction stress 20%
- foreign demand 15%

Missing components are disclosed. A sub-score is emitted with at least 60% configured weight; its coverage/quality/missing fields remain visible. All three sub-scores remain mandatory for TRRS.

## Indicator normalization

Each component uses:

```
0.50 × expanding historical percentile(level)
+ 0.30 × expanding historical percentile(momentum)
+ 0.20 × expanding historical percentile(acceleration)
```

Percentiles use 2000-to-t history including t and never see future observations.

## Bands
- 0–25 NORMAL
- 25–45 ELEVATED
- 45–65 WARNING
- 65–80 STRESS
- 80–100 REGIME BREAK RISK

## Acceleration override

A 3M TP move must pass both its bp floor and at least the 70th expanding historical percentile:
- +30bp → at least ELEVATED
- +50bp → at least WARNING
- +75bp → at least STRESS

The raw score, final score, applied flag and reason are in the JSON payload.
