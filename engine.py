#!/usr/bin/env python3
"""US Treasury Regime Risk Score (TRRS) engine.

Deterministic scoring only. No network, no fabricated values.
Percentiles are expanding-window from 2000 (inclusive of t).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

LEVEL_W, MOM_W, ACCEL_W = 0.50, 0.30, 0.20
MIN_HISTORY = 20
MIN_KPIS_FOR_TRRS = 6
MIN_WEIGHT_FOR_TRRS = 0.60
MOM_STABLE_BAND = 0.08  # 8bp monthly-equivalent 3m change treated as stable for TP

STATUS_BANDS = tuple((row["max"], row["label"]) for row in CONFIG["risk_bands"])

COLOR_BANDS = tuple((row["max"], row["color"]) for row in CONFIG["risk_bands"])

LAYERS = {
    "rate": "RATE DECOMPOSITION",
    "term_premium": "TERM PREMIUM",
    "fiscal": "FISCAL / SUPPLY",
    "transmission": "EQUITY / AI TRANSMISSION",
}


@dataclass(frozen=True)
class KpiSpec:
    key: str
    layer: str
    weight: float
    label_ko: str
    label_en: str
    higher_is_stress: bool
    frequency: str  # daily | monthly | quarterly
    unit: str
    source: str
    source_url: str
    series_id: str
    methodology: str
    interpretation: str
    mom_lag: int
    primary_model: bool = False


KPI_SPECS: list[KpiSpec] = [
    KpiSpec(
        key="dgs10",
        layer="rate",
        weight=0.12,
        label_ko="명목 10년물 금리",
        label_en="Nominal 10-Year Treasury Yield",
        higher_is_stress=True,
        frequency="daily",
        unit="%",
        source="Board of Governors of the Federal Reserve System (via FRED)",
        source_url="https://fred.stlouisfed.org/series/DGS10",
        series_id="DGS10",
        methodology="Market yield on U.S. Treasury securities at 10-year constant maturity, quoted on investment basis. Daily; dashboard scoring uses month-end.",
        interpretation="명목 장기금리가 오르면 할인율이 올라 성장주 멀티플이 눌릴 수 있다. 원인 분해 전에는 레벨만으로 레짐을 단정하지 않는다.",
        mom_lag=1,
    ),
    KpiSpec(
        key="dfii10",
        layer="rate",
        weight=0.12,
        label_ko="10년 실질금리 (TIPS)",
        label_en="10-Year Real Yield (TIPS)",
        higher_is_stress=True,
        frequency="daily",
        unit="%",
        source="Board of Governors of the Federal Reserve System (via FRED)",
        source_url="https://fred.stlouisfed.org/series/DFII10",
        series_id="DFII10",
        methodology="Market yield on Treasury inflation-indexed securities at 10-year constant maturity. Daily; scoring uses month-end.",
        interpretation="실질금리 상승은 성장 기대·정책금리 경로가 장기 할인율을 직접 올리는 경로다. 2013형 성장 쇼크에서 자주 주도한다.",
        mom_lag=1,
    ),
    KpiSpec(
        key="t10yie",
        layer="rate",
        weight=0.10,
        label_ko="10년 기대인플레이션 (BEI)",
        label_en="10-Year Breakeven Inflation",
        higher_is_stress=True,
        frequency="daily",
        unit="%",
        source="Federal Reserve Bank of St. Louis (via FRED)",
        source_url="https://fred.stlouisfed.org/series/T10YIE",
        series_id="T10YIE",
        methodology="10-year breakeven inflation rate: difference between nominal and TIPS yields. Not a survey of household inflation expectations.",
        interpretation="BEI가 같이 오르면 명목금리 상승의 일부가 인플레 기대이다. BEI가 정체인데 명목이 오르면 실질·텀프리미엄 쪽을 본다.",
        mom_lag=1,
    ),
    KpiSpec(
        key="fedfunds",
        layer="rate",
        weight=0.06,
        label_ko="유효 연방기금금리",
        label_en="Effective Federal Funds Rate",
        higher_is_stress=True,
        frequency="monthly",
        unit="%",
        source="Board of Governors of the Federal Reserve System (via FRED)",
        source_url="https://fred.stlouisfed.org/series/FEDFUNDS",
        series_id="FEDFUNDS",
        methodology="Effective federal funds rate, monthly average on FRED FEDFUNDS. Used as a policy-rate level context, not a term-premium substitute.",
        interpretation="정책금리 레벨은 기대 단기금리의 앵커다. 장기금리와 같이 오르면 정책 기대 경로, 괴리가 벌어지면 텀프리미엄을 의심한다.",
        mom_lag=1,
    ),
    KpiSpec(
        key="acm_tp10",
        layer="term_premium",
        weight=0.18,
        label_ko="ACM 10년 텀프리미엄 (대표 모델)",
        label_en="NY Fed ACM 10-Year Term Premium (primary)",
        higher_is_stress=True,
        frequency="daily",
        unit="%",
        source="Federal Reserve Bank of New York, ACM Treasury Term Premia",
        source_url="https://www.newyorkfed.org/research/data_indicators/term_premia.html",
        series_id="ACMTP10 (NY Fed ACM Daily workbook)",
        methodology="Adrian, Crump, Moench (2013) term premium on the 10-year zero. Model estimate, not a traded quote. Read from NY Fed ACMTermPremium.xls ACM Daily sheet, column ACMTP10. FRED ACMTP10 is not used because that graph id currently 404s.",
        interpretation="텀프리미엄은 만기 위험에 대한 보상이다. 레벨보다 짧은 기간의 가속이 레짐 전환 신호다.",
        mom_lag=1,
        primary_model=True,
    ),
    KpiSpec(
        key="three_month_tp",
        layer="term_premium",
        weight=0.08,
        label_ko="10년-3개월 스프레드 (근사)",
        label_en="10Y–3M Treasury Spread (slope proxy)",
        higher_is_stress=True,
        frequency="daily",
        unit="pp",
        source="Federal Reserve Bank of St. Louis (via FRED)",
        source_url="https://fred.stlouisfed.org/series/T10Y3M",
        series_id="T10Y3M",
        methodology="DGS10 minus TB3MS/3-month bill. This is a slope proxy, not an ACM term premium. Kept as an alternative observable when model estimates disagree.",
        interpretation="커브가 가팔라지며 장기물만 오르면 정책 기대보다 장기 수급·위험보상이 우세할 수 있다.",
        mom_lag=1,
    ),
    KpiSpec(
        key="federal_deficit",
        layer="fiscal",
        weight=0.12,
        label_ko="연방 재정적자 (12개월 합, GDP 대비)",
        label_en="Federal Budget Deficit, 12-month sum / GDP",
        higher_is_stress=True,
        frequency="annual",
        unit="% of GDP",
        source="U.S. Department of the Treasury / BEA (via FRED)",
        source_url="https://fred.stlouisfed.org/series/FYFSGDA188S",
        series_id="FYFSGDA188S",
        methodology="Federal surplus or deficit as percent of GDP (FYFSGDA188S). Sign flipped so a larger deficit is a higher stress reading. Annual series aligned to calendar year; monthly dashboards forward-fill within year.",
        interpretation="적자 확대는 국채 순발행 압력을 키운다. 적자 자체보다 발행이 소화되지 못할 때 텀프리미엄으로 전이된다.",
        mom_lag=1,
    ),
    KpiSpec(
        key="marketable_debt",
        layer="fiscal",
        weight=0.10,
        label_ko="시장성 국채 잔액 증가",
        label_en="Marketable Treasury Debt Outstanding (YoY)",
        higher_is_stress=True,
        frequency="monthly",
        unit="%",
        source="U.S. Department of the Treasury Fiscal Data",
        source_url="https://fiscaldata.treasury.gov/datasets/debt-to-the-penny/debt-to-the-penny",
        series_id="GFDEBTN / Debt to the Penny marketable",
        methodology="Year-over-year percent change in federal debt held by the public (GFDEBTN) as a supply proxy when Debt-to-the-Penny marketable detail is unavailable. Not privately estimated net issuance.",
        interpretation="공급이 빠르게 늘면 딜러·해외 수요가 소화해야 할 물량이 커져 텀프리미엄이 붙을 수 있다.",
        mom_lag=12,
    ),
    KpiSpec(
        key="move_proxy",
        layer="transmission",
        weight=0.06,
        label_ko="장기금리 실현 변동성 (60일)",
        label_en="10Y Realized Volatility (60-day)",
        higher_is_stress=True,
        frequency="daily",
        unit="pp",
        source="Derived from FRED DGS10",
        source_url="https://fred.stlouisfed.org/series/DGS10",
        series_id="DGS10 60d stdev",
        methodology="60-session standard deviation of daily changes in DGS10, annualized by sqrt(252). Official MOVE index is not used because it is not a free government series.",
        interpretation="금리 변동성이 커지면 주식 리스크 프리미엄과 헤지 수요가 같이 움직인다.",
        mom_lag=20,
    ),
    KpiSpec(
        key="hy_oas",
        layer="transmission",
        weight=0.06,
        label_ko="하이일드 스프레드",
        label_en="ICE BofA US High Yield OAS",
        higher_is_stress=True,
        frequency="daily",
        unit="pp",
        source="ICE BofA via FRED",
        source_url="https://fred.stlouisfed.org/series/BAMLH0A0HYM2",
        series_id="BAMLH0A0HYM2",
        methodology="ICE BofA US High Yield Index Option-Adjusted Spread. Used as credit-transmission, not as a Treasury term premium.",
        interpretation="국채 스트레스가 신용 스프레드로 번지면 주식·AI 캡엑스 자금조달 비용이 같이 올라간다.",
        mom_lag=20,
    ),
]


KPI_BY_KEY = {spec.key: spec for spec in KPI_SPECS}
WEIGHT_SUM = round(sum(s.weight for s in KPI_SPECS), 10)
assert abs(WEIGHT_SUM - 1.0) < 1e-12, WEIGHT_SUM


def expanding_percentile(history: np.ndarray, value: float, invert: bool) -> float | None:
    hist = np.asarray(history, dtype="float64")
    hist = hist[np.isfinite(hist)]
    if hist.size < MIN_HISTORY or not np.isfinite(value):
        return None
    if float(np.nanmax(hist) - np.nanmin(hist)) < 1e-12:
        return 50.0
    rank = float(np.mean(hist <= value) * 100.0)
    if invert:
        rank = 100.0 - rank
    return float(np.clip(rank, 0.0, 100.0))


def _window_change(s: pd.Series, lag: int) -> pd.Series:
    return s - s.shift(lag)


def _lag_for_horizon(freq: str, horizon: str) -> int | None:
    """Approximate period lags for 1W/1M/3M/6M/YoY on the scoring index."""
    table = {
        "daily": {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "yoy": 252},
        "monthly": {"1w": None, "1m": 1, "3m": 3, "6m": 6, "yoy": 12},
        "quarterly": {"1w": None, "1m": None, "3m": 1, "6m": 2, "yoy": 4},
    }
    return table.get(freq, table["monthly"]).get(horizon)


def score_series(spec: KpiSpec, series: pd.Series) -> pd.DataFrame:
    s = series.dropna().astype("float64").sort_index()
    s = s[~s.index.duplicated(keep="last")]
    s = s[s.index >= "2000-01-01"]
    lag = spec.mom_lag
    mom = _window_change(s, lag)
    accel = mom - mom.shift(lag)
    invert = not spec.higher_is_stress
    chg = {}
    for name in ("1w", "1m", "3m", "6m", "yoy"):
        hlag = _lag_for_horizon(spec.frequency, name)
        chg[name] = _window_change(s, hlag) if hlag else pd.Series(index=s.index, dtype="float64")
    rows = []
    vals = s.to_numpy()
    idx = s.index
    mom_v = mom.to_numpy()
    acc_v = accel.to_numpy()
    for i in range(len(s)):
        level = expanding_percentile(vals[: i + 1], vals[i], invert)
        m = expanding_percentile(mom_v[: i + 1], mom_v[i], invert) if np.isfinite(mom_v[i]) else None
        a = expanding_percentile(acc_v[: i + 1], acc_v[i], invert) if np.isfinite(acc_v[i]) else None
        parts, weights = [], []
        if level is not None:
            parts.append(LEVEL_W * level)
            weights.append(LEVEL_W)
        if m is not None:
            parts.append(MOM_W * m)
            weights.append(MOM_W)
        if a is not None:
            parts.append(ACCEL_W * a)
            weights.append(ACCEL_W)
        score = None
        if weights and abs(sum(weights) - 1.0) < 1e-9:
            score = float(sum(parts))
        elif weights and LEVEL_W in weights:
            score = float(sum(parts) / sum(weights))
        prev = float(vals[i - 1]) if i else None
        row = {
            "date": idx[i],
            "value": float(vals[i]),
            "previous": prev,
            "change": None if prev is None else float(vals[i] - prev),
            "level": level,
            "momentum": m,
            "acceleration": a,
            "score": score,
        }
        for name in ("1w", "1m", "3m", "6m", "yoy"):
            series_chg = chg[name]
            v = series_chg.iloc[i] if i < len(series_chg) else np.nan
            row[f"chg_{name}"] = None if not np.isfinite(v) else float(v)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date")


def status_label(score: float | None) -> str:
    if score is None or not np.isfinite(score):
        return "UNAVAILABLE"
    for cut, label in STATUS_BANDS:
        if score < cut:
            return label
    return "TREASURY REGIME SHOCK"


def color_band(score: float | None) -> str:
    if score is None or not np.isfinite(score):
        return "muted"
    for cut, name in COLOR_BANDS:
        if score < cut:
            return name
    return "red"


def decompose_nominal(
    nominal: pd.Series,
    real: pd.Series | None,
    bei: pd.Series | None,
    expected_short: pd.Series | None = None,
    term_premium: pd.Series | None = None,
) -> pd.DataFrame:
    """Align identities without inventing missing legs."""
    idx = nominal.dropna().index
    out = pd.DataFrame(index=idx)
    out["nominal"] = nominal.reindex(idx)
    if real is not None:
        out["real"] = real.reindex(idx)
    if bei is not None:
        out["bei"] = bei.reindex(idx)
    if "real" in out and "bei" in out:
        out["residual"] = out["nominal"] - out["real"] - out["bei"]
        move = out["nominal"].diff().abs()
        real_d = out["real"].diff().abs()
        bei_d = out["bei"].diff().abs()
        res_d = out["residual"].diff().abs()
        denom = real_d + bei_d + res_d
        out["real_share"] = np.where(denom > 1e-9, real_d / denom, np.nan)
        out["bei_share"] = np.where(denom > 1e-9, bei_d / denom, np.nan)
        out["residual_share"] = np.where(denom > 1e-9, res_d / denom, np.nan)
        # first row has no change; still define shares so tests on latest work
        if len(out) and not np.isfinite(out["real_share"].iloc[-1]):
            r = abs(float(out["real"].iloc[-1]))
            b = abs(float(out["bei"].iloc[-1]))
            e = abs(float(out["residual"].iloc[-1]))
            tot = r + b + e
            if tot > 1e-9:
                out.loc[out.index[-1], "real_share"] = r / tot
                out.loc[out.index[-1], "bei_share"] = b / tot
                out.loc[out.index[-1], "residual_share"] = e / tot
    if expected_short is not None:
        out["expected_short"] = expected_short.reindex(idx)
    if term_premium is not None:
        out["term_premium"] = term_premium.reindex(idx)
    return out


def tp_acceleration_state(tp: pd.Series, lookback: int = 3) -> dict[str, Any]:
    s = tp.dropna().astype("float64").sort_index()
    if len(s) < lookback + 2:
        return {"state": "UNAVAILABLE", "acceleration": False, "chg_3m": None, "chg_6m": None}
    chg_3m = float(s.iloc[-1] - s.iloc[-1 - lookback]) if len(s) > lookback else None
    chg_6m = float(s.iloc[-1] - s.iloc[-1 - 2 * lookback]) if len(s) > 2 * lookback else None
    prev_3m = float(s.iloc[-1 - lookback] - s.iloc[-1 - 2 * lookback]) if len(s) > 2 * lookback else 0.0
    accelerating = bool(chg_3m is not None and chg_3m > 0.30 and chg_3m > prev_3m + 0.05)
    if accelerating:
        state = "ACCELERATING"
    elif chg_3m is not None and abs(chg_3m) <= MOM_STABLE_BAND:
        state = "STABLE"
    elif chg_3m is not None and chg_3m < -0.15:
        state = "DECELERATING"
    else:
        state = "RISING" if (chg_3m or 0) > 0 else "STABLE"
    return {
        "state": state,
        "acceleration": accelerating,
        "chg_3m": chg_3m,
        "chg_6m": chg_6m,
        "level": float(s.iloc[-1]),
    }


def classify_regime(
    real_chg_3m: float | None,
    bei_chg_3m: float | None,
    tp_chg_3m: float | None,
    tp_accelerating: bool,
    deficit_stress: float | None,
) -> dict[str, Any]:
    real_chg_3m = float(real_chg_3m or 0)
    bei_chg_3m = float(bei_chg_3m or 0)
    tp_chg_3m = float(tp_chg_3m or 0)
    deficit_stress = float(deficit_stress or 0)
    fiscal = deficit_stress >= 70 and tp_accelerating and tp_chg_3m >= 0.4
    if fiscal:
        regime = "FISCAL-CONFIDENCE SHOCK"
        note = "적자·공급 스트레스가 높고 텀프리미엄이 가속한다. 정책 기대만의 조정이 아니다."
    elif tp_accelerating and tp_chg_3m >= 0.45 and tp_chg_3m >= real_chg_3m and tp_chg_3m >= bei_chg_3m:
        regime = "2023-LIKE TERM PREMIUM SHOCK"
        note = "텀프리미엄이 명목금리 상승을 주도한다. 실질·BEI보다 수급·만기 위험이 크다."
    elif real_chg_3m >= 0.45 and real_chg_3m > tp_chg_3m and real_chg_3m > bei_chg_3m:
        regime = "2013-LIKE GROWTH SHOCK"
        note = "실질금리 상승이 주도한다. 성장 기대·정책 경로 재가격에 가깝다."
    elif bei_chg_3m >= 0.45 and bei_chg_3m > real_chg_3m and bei_chg_3m > tp_chg_3m:
        regime = "INFLATION-EXPECTATION SHOCK"
        note = "손익분기 인플레가 명목금리 상승을 설명한다."
    else:
        regime = "MIXED / UNCLEAR"
        note = "한 축이 뚜렷하지 않다. 모델 분산과 개별 KPI를 같이 본다."
    return {"regime": regime, "note": note}


def _layer_score(present: dict[str, float], layer: str) -> float | None:
    keys = [k for k in present if KPI_BY_KEY[k].layer == layer]
    if not keys:
        return None
    w = sum(KPI_BY_KEY[k].weight for k in keys)
    if w <= 0:
        return None
    return float(sum(present[k] * KPI_BY_KEY[k].weight for k in keys) / w)


def combine_scores(scored: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for key, spec in KPI_BY_KEY.items():
        df = scored.get(key)
        if df is None or df.empty or "score" not in df:
            continue
        col = df["score"].rename(key)
        if spec.frequency == "daily":
            col = col.resample("MS").last()
        elif spec.frequency == "quarterly":
            col = col.resample("MS").ffill()
        frames.append(col)
    if not frames:
        return pd.DataFrame(columns=["trrs", "rate_stress", "tp_stress", "fiscal_stress", "available_weight", "n_kpis", "confidence"])
    wide = pd.concat(frames, axis=1).sort_index()
    wide = wide[wide.index >= "2000-01-01"]
    rows = []
    for dt, row in wide.iterrows():
        present = {k: float(row[k]) for k in row.index if pd.notna(row[k])}
        weight = sum(KPI_BY_KEY[k].weight for k in present)
        layers = {KPI_BY_KEY[k].layer for k in present}
        n = len(present)
        rate = _layer_score(present, "rate")
        tp = _layer_score(present, "term_premium")
        fiscal = _layer_score(present, "fiscal")
        transmission = _layer_score(present, "transmission")
        usable = (
            n >= MIN_KPIS_FOR_TRRS
            and weight >= MIN_WEIGHT_FOR_TRRS
            and rate is not None
            and tp is not None
            and fiscal is not None
        )
        trrs = None
        if usable:
            fw = CONFIG["final_weights"]
            trrs = float(rate * fw["rate"] + tp * fw["term_premium"] + fiscal * fw["fiscal"])
        if n == len(KPI_SPECS) and abs(weight - 1.0) < 1e-9:
            conf = "HIGH"
        elif n >= 8:
            conf = "MEDIUM"
        else:
            conf = "LOW"
        if not usable:
            conf = "LOW"
        rows.append(
            {
                "date": dt,
                "trrs": trrs,
                "rate_stress": rate,
                "tp_stress": tp,
                "fiscal_stress": fiscal,
                "transmission_stress": transmission,
                "available_weight": float(weight),
                "n_kpis": n,
                "confidence": conf,
                "usable": usable,
            }
        )
    return pd.DataFrame(rows).set_index("date")


def rate_stress(hist: pd.DataFrame) -> float | None:
    if hist.empty or "rate_stress" not in hist:
        return None
    v = hist["rate_stress"].dropna()
    return None if v.empty else float(v.iloc[-1])


def term_premium_stress(hist: pd.DataFrame) -> float | None:
    if hist.empty or "tp_stress" not in hist:
        return None
    v = hist["tp_stress"].dropna()
    return None if v.empty else float(v.iloc[-1])


def fiscal_confidence_stress(hist: pd.DataFrame) -> float | None:
    if hist.empty or "fiscal_stress" not in hist:
        return None
    v = hist["fiscal_stress"].dropna()
    return None if v.empty else float(v.iloc[-1])


def treasury_regime_risk(hist: pd.DataFrame) -> float | None:
    if hist.empty or "trrs" not in hist:
        return None
    v = hist["trrs"].dropna()
    return None if v.empty else float(v.iloc[-1])


def model_dispersion(models: dict[str, float | None]) -> dict[str, Any]:
    vals = [float(v) for v in models.values() if v is not None and np.isfinite(v)]
    if len(vals) < 2:
        return {"n": len(vals), "max": None if not vals else vals[0], "min": None if not vals else vals[0], "range": None, "uncertainty": "LOW" if vals else "UNAVAILABLE"}
    mx, mn = max(vals), min(vals)
    spread = mx - mn
    if spread >= 0.60:
        flag = "HIGH"
    elif spread >= 0.30:
        flag = "MEDIUM"
    else:
        flag = "LOW"
    return {"n": len(vals), "max": mx, "min": mn, "range": spread, "uncertainty": flag}


def latest_change(series: pd.Series, lag: int) -> float | None:
    s = series.dropna()
    if len(s) <= lag:
        return None
    return float(s.iloc[-1] - s.iloc[-1 - lag])


def classify_quality(spec: KpiSpec, series: pd.Series, as_of: pd.Timestamp, now: pd.Timestamp) -> tuple[str, str]:
    if series is None or series.dropna().empty:
        return "MISSING", "공식 시계열이 비어 있어 점수를 계산하지 않습니다."
    latest = pd.Timestamp(series.dropna().index.max())
    if spec.frequency == "daily":
        stale_after = pd.DateOffset(days=14)
    elif spec.frequency == "quarterly":
        stale_after = pd.DateOffset(months=8)
    elif spec.frequency in {"annual", "publication"}:
        stale_after = pd.DateOffset(days=550)
    else:
        stale_after = pd.DateOffset(months=4)
    if latest < now - stale_after:
        return "STALE", f"최신 관측 {latest.date()} 이(가) 발표 주기 대비 오래되었습니다."
    if len(series.dropna()) < MIN_HISTORY:
        return "MISSING", "역사가 짧아 점수 계산 최소 표본에 못 미칩니다. 값은 표시하되 점수에는 넣지 않습니다."
    if spec.primary_model:
        return "ESTIMATED", "NY Fed ACM 모델 추정치이며 직접 관측되는 시장가격이 아닙니다."
    return "VALID", ""


def latest_row(df: pd.DataFrame | None) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    return {k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in row.to_dict().items()}


def basis_points(percentage_points: float | None) -> float | None:
    """Convert percentage points to basis points (1.00pp = 100bp)."""
    if percentage_points is None or not np.isfinite(percentage_points):
        return None
    return float(percentage_points) * 100.0


def rolling_zscore(series: pd.Series, window: int = 252, min_periods: int = 60) -> pd.Series:
    """Backward-looking rolling z-score; no centered windows or future data."""
    s = series.astype("float64").sort_index()
    mean = s.rolling(window, min_periods=min_periods).mean()
    std = s.rolling(window, min_periods=min_periods).std(ddof=0)
    return ((s - mean) / std.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def _linear_slope(values: np.ndarray) -> float | None:
    y = np.asarray(values, dtype="float64")
    y = y[np.isfinite(y)]
    if y.size < 3:
        return None
    x = np.arange(y.size, dtype="float64")
    return float(np.polyfit(x, y, 1)[0])


def term_premium_slopes(tp: pd.Series) -> dict[str, Any]:
    s = tp.dropna().astype("float64").sort_index()
    slope20 = _linear_slope(s.iloc[-20:].to_numpy()) if len(s) >= 20 else None
    slope60 = _linear_slope(s.iloc[-60:].to_numpy()) if len(s) >= 60 else None
    s20_bp = basis_points(slope20)
    s60_bp = basis_points(slope60)
    accelerating = bool(
        s20_bp is not None
        and s60_bp is not None
        and s20_bp > 0
        and s20_bp > s60_bp + 0.10
    )
    return {
        "slope_20_bp_per_day": s20_bp,
        "slope_60_bp_per_day": s60_bp,
        "accelerating": accelerating,
    }


def apply_acceleration_override(
    base_score: float | None,
    change_3m_bp: float | None,
    historical_percentile: float | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply configured minimum band only when both bp floor and historical calibration agree."""
    config = config or CONFIG
    if base_score is None:
        return {"score": None, "applied": False, "minimum_band": None, "reason": "base score unavailable"}
    gate = float(config["acceleration_override"]["calibration_percentile_min"])
    if change_3m_bp is None or historical_percentile is None or historical_percentile < gate:
        return {"score": float(base_score), "applied": False, "minimum_band": None, "reason": "calibration gate not met"}
    selected = None
    for rule in sorted(config["acceleration_override"]["rules"], key=lambda r: r["change_3m_bp"]):
        if change_3m_bp >= float(rule["change_3m_bp"]):
            selected = rule
    if selected is None:
        return {"score": float(base_score), "applied": False, "minimum_band": None, "reason": "3M change below floor"}
    minimum_score = float(selected["minimum_score"])
    return {
        "score": max(float(base_score), minimum_score),
        "applied": float(base_score) < minimum_score,
        "minimum_band": selected["minimum_band"],
        "reason": f"3M TP +{change_3m_bp:.0f}bp, expanding percentile {historical_percentile:.0f}",
    }


def _expanding_percentile_series(s: pd.Series, min_history: int = MIN_HISTORY) -> pd.Series:
    vals = s.to_numpy(dtype="float64")
    out = []
    for i, value in enumerate(vals):
        if not np.isfinite(value):
            out.append(np.nan)
            continue
        p = expanding_percentile(vals[: i + 1], value, False)
        out.append(np.nan if p is None or i + 1 < min_history else p)
    return pd.Series(out, index=s.index, dtype="float64")


def _event_dates(mask: pd.Series, cooldown: int) -> list[pd.Timestamp]:
    mask = mask.fillna(False).astype(bool)
    positions = np.flatnonzero(mask.to_numpy())
    selected: list[int] = []
    for pos in positions:
        if not selected or pos - selected[-1] >= cooldown:
            selected.append(int(pos))
    return [pd.Timestamp(mask.index[pos]) for pos in selected]


def _forward_statistics(prices: pd.Series, events: list[pd.Timestamp], horizon: int) -> dict[str, Any]:
    px = prices.dropna().astype("float64").sort_index()
    returns, drawdowns = [], []
    for event in events:
        start_pos = int(px.index.searchsorted(event, side="left"))
        if start_pos >= len(px) or start_pos + horizon >= len(px):
            continue
        path = px.iloc[start_pos : start_pos + horizon + 1]
        p0 = float(path.iloc[0])
        if p0 <= 0:
            continue
        returns.append((float(path.iloc[-1]) / p0 - 1.0) * 100.0)
        drawdowns.append(float(((path / path.cummax()) - 1.0).min() * 100.0))
    if not returns:
        return {"average": None, "median": None, "win_rate": None, "max_drawdown": None, "sample_size": 0, "p25": None, "p75": None}
    arr = np.asarray(returns, dtype="float64")
    return {
        "average": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "win_rate": float(np.mean(arr > 0) * 100.0),
        "max_drawdown": float(np.min(drawdowns)),
        "sample_size": int(arr.size),
        "p25": float(np.percentile(arr, 25)),
        "p75": float(np.percentile(arr, 75)),
    }


def backtest_events(
    tp: pd.Series,
    spx: pd.Series,
    nasdaq: pd.Series,
    start: str = "2000-01-01",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mechanical TP shock definitions and forward returns, using only data known at each event date."""
    config = config or CONFIG
    tp = tp.dropna().astype("float64").sort_index()
    tp = tp[tp.index >= pd.Timestamp(start)]
    if tp.empty:
        return {"definitions": {}, "note": "Term premium history unavailable"}
    defs = config["event_definitions"]
    chg63 = tp - tp.shift(63)
    chg_pct = _expanding_percentile_series(chg63)
    z = rolling_zscore(tp, 252, 60)
    level_pct = _expanding_percentile_series(tp)
    masks = {
        "3m_percentile": chg_pct >= float(defs["change_3m_percentile"]),
        "3m_50bp": basis_points_series(chg63) >= float(defs["change_3m_bp_floor"]),
        "zscore": z >= float(defs["zscore_threshold"]),
        "level_momentum": (level_pct >= float(defs["level_percentile"])) & (chg_pct >= float(defs["change_3m_percentile"])),
    }
    masks["union"] = masks["3m_percentile"] | masks["3m_50bp"] | masks["zscore"] | masks["level_momentum"]
    cooldown = int(defs["cooldown_trading_days"])
    horizons = config["backtest_horizons_trading_days"]
    out = {}
    for name, mask in masks.items():
        events = _event_dates(mask, cooldown)
        out[name] = {
            "sample_size": len(events),
            "event_dates": [d.strftime("%Y-%m-%d") for d in events],
            "horizons": {},
        }
        for label, n_days in horizons.items():
            out[name]["horizons"][label] = {
                "spx": _forward_statistics(spx, events, int(n_days)),
                "nasdaq": _forward_statistics(nasdaq, events, int(n_days)),
            }
            # compatibility summary uses S&P 500 for top-level fields
            out[name]["horizons"][label].update(_forward_statistics(spx, events, int(n_days)))
    return {
        "definitions": out,
        "note": "Expanding percentiles and backward rolling windows only; 63-trading-day cooldown prevents overlapping event spam.",
    }


def basis_points_series(series: pd.Series) -> pd.Series:
    return series.astype("float64") * 100.0


def latest_stress_score(
    series: pd.Series | None,
    *,
    higher_is_stress: bool = True,
    momentum_lag: int = 3,
) -> float | None:
    """Latest 0-100 level/momentum/acceleration score for a derived series."""
    if series is None:
        return None
    s = series.dropna().astype("float64").sort_index()
    if len(s) < MIN_HISTORY:
        return None
    values = s.to_numpy()
    momentum = (s - s.shift(momentum_lag)).to_numpy()
    acceleration = ((s - s.shift(momentum_lag)) - (s.shift(momentum_lag) - s.shift(2 * momentum_lag))).to_numpy()
    invert = not higher_is_stress
    level = expanding_percentile(values, values[-1], invert)
    mom = expanding_percentile(momentum[np.isfinite(momentum)], momentum[-1], invert) if np.isfinite(momentum[-1]) else None
    accel = expanding_percentile(acceleration[np.isfinite(acceleration)], acceleration[-1], invert) if np.isfinite(acceleration[-1]) else None
    parts = [(LEVEL_W, level), (MOM_W, mom), (ACCEL_W, accel)]
    usable = [(w, v) for w, v in parts if v is not None]
    if not usable or level is None:
        return None
    return float(sum(w * float(v) for w, v in usable) / sum(w for w, _ in usable))


def weighted_component_score(
    scores: dict[str, float | None],
    weights: dict[str, float],
    *,
    minimum_coverage: float = 0.60,
) -> dict[str, Any]:
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("component weights must sum to 1")
    unknown = set(scores) - set(weights)
    if unknown:
        raise ValueError(f"unknown component scores: {sorted(unknown)}")
    present = {k: float(v) for k, v in scores.items() if v is not None and np.isfinite(v)}
    coverage = float(sum(weights[k] for k in present))
    score = None
    if coverage >= minimum_coverage and present:
        score = float(sum(present[k] * weights[k] for k in present) / coverage)
    return {
        "score": score,
        "coverage": coverage,
        "available": sorted(present),
        "missing": sorted(set(weights) - set(present)),
        "quality": "VALID" if coverage >= 0.999999 else ("PARTIAL" if score is not None else "MISSING"),
    }
