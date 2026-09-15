# DATA_SOURCES

## Rate structure
- DGS2, DGS10, DGS30 — Federal Reserve Board via FRED, daily
- DFII10 — 10Y TIPS real yield, Fed Board via FRED, daily
- T10YIE — 10Y breakeven, FRED, daily
- FEDFUNDS — effective federal funds monthly average
- Policy expectation proxy — DGS2 minus last available FEDFUNDS; **not Fed futures**
- Expected-short component — DGS10 minus NY Fed ACM TP10; model residual, not an independent quote

## Term premium
- Primary: NY Fed `ACMTermPremium.xls`, sheet `ACM Daily`, field `ACMTP10`
- Alternative: Federal Reserve Kim-Wright `THREEFYTP10` via FRED
- MOVE: MISSING. No official/free government series is substituted.
- Yield volatility proxy: 60-session annualized stdev of daily DGS10 changes

## Fiscal / supply
- FYFSGDA188S — deficit/GDP, annual fiscal year
- FYOINT / FYFR — federal interest outlays / receipts, annual fiscal year
- FYOIGDA188S — interest outlays/GDP, annual
- GFDEGDQ188S — total public debt/GDP, quarterly
- Fiscal Data Debt to the Penny `debt_to_penny` — debt held by public, daily
- Fiscal Data `auctions_query` — official auction and accepted issuance records, 2008-04 onward for bidder-field consistency

## Auction
Endpoint: `https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query`

Tenors: 2Y, 5Y, 7Y, 10Y, 20Y, 30Y nominal coupons. Reopenings use `original_security_term`. Direct/indirect/dealer shares use `comp_accepted` as denominator. Rolling windows mean the latest 3/6 **auctions**, not calendar months.

When-issued yield and auction tail are MISSING. Treasury/Fed official free feeds do not provide a stable auction-immediate WI series; no par-curve proxy is used.

## Transmission
- BAMLC0A0CM, BAMLH0A0HYM2 — ICE BofA IG/HY OAS via FRED
- VIXCLS — CBOE VIX via FRED
- DTWEXBGS — broad dollar index via FRED
- SP500, NASDAQCOM, USREC — FRED
- Gold: MISSING; former FRED source currently 404s and no unofficial source is substituted.

## Not yet scored
- TIC foreign Treasury holdings/share
- NY Fed primary dealer positions
- Treasury quarterly net marketable borrowing estimates

They are shown as missing rather than estimated.
