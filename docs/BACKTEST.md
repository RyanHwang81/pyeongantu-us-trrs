# BACKTEST

## Event definitions

The dashboard tests four mechanical definitions plus their union:
1. 3M ACM TP change at/above expanding 90th percentile
2. 3M ACM TP change above +50bp
3. 1Y rolling TP z-score above 1.5
4. TP level percentile above 75 and 3M momentum percentile above 90

Signals are separated by a 63-trading-day cooldown. The event detector uses only backward rolling or expanding information available at each date.

## Horizons and statistics

For S&P 500 and Nasdaq:
- 1W / 5 trading days
- 1M / 21
- 3M / 63
- 6M / 126
- 12M / 252

Outputs: average, median, win rate, max drawdown within the holding window, sample size, p25 and p75.

## Limitations

- FRED SP500 history may be shorter than ACM history, so actual horizon sample counts can be lower than event candidate counts.
- Historical returns are descriptive, not a causal estimate.
- Fiscal/supply regime backtests are not yet fully classified because long official auction bidder fields begin consistently in 2008 and foreign-demand history is not yet connected.
- Fixed case windows (2013, 2022, 2023) are context, separate from mechanical event tests.
