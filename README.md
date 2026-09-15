# 평안투 US Treasury Regime Risk Dashboard

미국 10년물 국채금리를 하나의 숫자로 보여 주지 않습니다. **명목금리 = 기대 단기금리 + 텀프리미엄**, 동시에 **명목 ≈ 실질 + BEI**로 분해하고, 레벨·모멘텀·가속·재정 공급·주식 전염을 봅니다.

## 공개 산출물

| 파일 | 용도 |
|---|---|
| `dist/index.html` | 공개 대시보드 (GitHub Pages / 블로그 iframe) |
| `dist/trrs_data.json` | 계산 결과 원본 |

방법론: `docs/methodology.md`

예정 공개 URL: `https://ryanhwang81.github.io/pyeongantu-us-trrs/`

## 점수

- **Treasury Regime Risk Score (TRRS)** 0–100
- **Rate Stress Score** — 명목·실질·BEI·정책금리
- **Term Premium Stress Score** — ACM 대표 + 커브 기울기
- **Fiscal Confidence Stress Score** — 적자/GDP, 공공부채 YoY

없는 공식 시계열은 추정하지 않고 공란입니다. 텀프리미엄은 모델 추정치임을 화면에 표시합니다.

## 로컬 빌드

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 build.py --out dist
```

환경변수 `TRRS_CACHE=/path` 가 있으면 원자료를 캐시합니다.

## 자동 갱신

금리 데이터는 일간이지만 FRED 확정·텀프리미엄 갱신 주기를 맞춰 **매월 1일·16일 09:00 UTC**에 빌드합니다. 신규 데이터가 없으면 dist 커밋 없이 종료합니다. FRED graph CSV는 GitHub-hosted runner에서 timeout 되는 경우가 있어 `self-hosted, macOS, gl-monitor` runner를 사용합니다.

## 고지

이 모니터는 공개 1차 자료를 이용한 시장 해석 도구입니다. 투자 자문이 아니며, 과거 구간 분류가 미래 성과를 보장하지 않습니다.
