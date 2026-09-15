# US Treasury Regime Risk — methodology

이 문서는 공개 HTML과 같은 산식을 설명합니다. 공개 페이지는 방법론 전문을 넣지 않고, 각 KPI 카드에서 정의·출처를 엽니다.

## Identities

```
10Y nominal ≈ expected future short rates + term premium
10Y nominal ≈ 10Y real (TIPS) + 10Y breakeven inflation
```

두 식을 억지로 한 숫자로 합치지 않습니다. ACM 텀프리미엄과 TIPS 분해는 다른 정의입니다.

## Primary sources

| Series | Source | Role |
|---|---|---|
| DGS10 | FRED / Fed Board | Nominal 10Y |
| DFII10 | FRED / Fed Board | Real 10Y |
| T10YIE | FRED | 10Y BEI |
| FEDFUNDS | FRED | Policy rate context |
| ACMTP10 | NY Fed ACM via FRED | Primary term premium |
| THREEFYTP10 | Fed Kim-Wright via FRED | Alternate term premium |
| T10Y3M | FRED | Slope proxy, not ACM |
| FYFSGDA188S | FRED / Treasury-BEA | Federal surplus/deficit % GDP (sign flipped) |
| GFDEBTN | FRED | Debt held by the public, YoY supply proxy |
| DGS10 60d vol | derived | Realized rate vol; MOVE is not a free official series |
| BAMLH0A0HYM2 | ICE via FRED | Credit transmission |
| SP500, NASDAQCOM, USREC | FRED | Equity windows / recession shading |

San Francisco Fed Treasury Yield Premium은 안정적 공개 CSV가 확인되지 않으면 넣지 않습니다. 공란으로 두고 추정하지 않습니다.

## Scoring

각 KPI 점수 = 0.50 × level percentile + 0.30 × momentum percentile + 0.20 × acceleration percentile.

퍼센타일은 2000년 이후 **expanding window (t 포함)** 입니다. 미래 값을 보지 않습니다.

TRRS는 레이어 가중 평균입니다. 최소 6개 KPI, 가중치 합 60%, rate와 term_premium 레이어가 있어야 계산합니다. 부족하면 TRRS는 공란입니다.

## Regime rules (pre-specified)

- **2013-like growth shock:** 3M Δ real ≥ 0.45pp and real dominates BEI and ACM TP
- **2023-like term-premium shock:** ACM TP accelerating, 3M Δ TP ≥ 0.45pp and dominates real/BEI
- **Fiscal-confidence shock:** fiscal stress ≥ 70 and TP accelerating with 3M Δ TP ≥ 0.40pp
- **Inflation-expectation shock:** 3M Δ BEI dominates
- else **mixed / unclear**

임계값은 사후 최적화하지 않습니다.

## Acceleration

월말 ACM TP의 3개월 변화가 +30bp를 넘고, 직전 3개월 변화보다 커지면 `ACCELERATING`. 고레벨이 가만히 있으면 `STABLE`.

## What this dashboard does not do

- 뉴스·IB 리포트를 원자료로 쓰지 않습니다.
- 하이퍼스케일러 CapEx 분기 공시를 월간 KPI로 섞지 않습니다.
- 결측을 보간해 실제값처럼 보여 주지 않습니다.
