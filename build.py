#!/usr/bin/env python3
"""Fetch official Treasury series, compute TRRS, render the public dashboard."""
from __future__ import annotations

import argparse
import io
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from engine import (
    CONFIG,
    KPI_SPECS,
    apply_acceleration_override,
    backtest_events,
    basis_points,
    classify_quality,
    classify_regime,
    color_band,
    combine_scores,
    decompose_nominal,
    expanding_percentile,
    fiscal_confidence_stress,
    latest_change,
    latest_row,
    latest_stress_score,
    model_dispersion,
    rate_stress,
    rolling_zscore,
    score_series,
    status_label,
    term_premium_slopes,
    term_premium_stress,
    tp_acceleration_state,
    treasury_regime_risk,
    weighted_component_score,
)
from alerts import evaluate_alerts
from official_data import (
    AUCTION_FIELDS,
    auction_metrics,
    auction_stress_history,
    debt_held_public_series,
    fiscal_rows,
    issuance_monthly,
    parse_auction_rows,
)

ROOT = Path(__file__).resolve().parent
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
ACM_XLS = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"
UA = {"User-Agent": "Mozilla/5.0 (compatible; pyeongantoo-trrs-builder/1.0)"}
CACHE = os.environ.get("TRRS_CACHE", "")

try:
    import certifi

    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    CTX = ssl.create_default_context()


def log(*a):
    print("[data]", *a, flush=True)


def fetch(url: str, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX, timeout=45) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"다운로드 실패: {url} ({last})")


def cached_get(name: str, url: str) -> bytes:
    if CACHE:
        path = Path(CACHE) / name
        if path.exists():
            return path.read_bytes()
        data = fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
    return fetch(url)


def fred(series_id: str) -> pd.Series:
    try:
        raw = cached_get(f"{series_id}.csv", FRED.format(series_id)).decode("utf-8", "replace")
    except Exception as e:
        log("FRED miss", series_id, e)
        return pd.Series(dtype="float64")
    d = pd.read_csv(io.StringIO(raw))
    if d.shape[1] < 2:
        return pd.Series(dtype="float64")
    d.columns = ["date", "value"]
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d["date"] = pd.to_datetime(d["date"])
    return d.dropna().set_index("date")["value"].sort_index()


def month_end(s: pd.Series) -> pd.Series:
    """Last observation per month while preserving the real observation date."""
    if s is None or s.dropna().empty:
        return pd.Series(dtype="float64")
    clean = s.dropna().sort_index()
    rows = []
    for _, group in clean.groupby(clean.index.to_period("M")):
        rows.append((pd.Timestamp(group.index[-1]), float(group.iloc[-1])))
    return pd.Series({date: value for date, value in rows}, dtype="float64").sort_index()


def fiscal_year_end(s: pd.Series) -> pd.Series:
    """FRED OMB annual observations use Jan-01 labels for the fiscal year."""
    if s is None or s.dropna().empty:
        return pd.Series(dtype="float64")
    out = s.dropna().copy()
    out.index = pd.to_datetime([f"{pd.Timestamp(i).year}-09-30" for i in out.index])
    return out.sort_index()


def parse_acm_xls(data: bytes) -> pd.Series:
    """NY Fed ACM workbook: column ACMTP10 on the ACM Daily sheet."""
    xl = pd.ExcelFile(io.BytesIO(data), engine="xlrd")
    sheet = "ACM Daily" if "ACM Daily" in xl.sheet_names else xl.sheet_names[0]
    df = pd.read_excel(xl, sheet_name=sheet, header=0)
    cols = {str(c).strip().upper(): c for c in df.columns}
    date_col = cols.get("DATE")
    tp_col = cols.get("ACMTP10")
    if date_col is None or tp_col is None:
        raise RuntimeError(f"ACM workbook missing DATE/ACMTP10: {list(df.columns)[:12]}")
    d = pd.to_datetime(df[date_col], errors="coerce")
    v = pd.to_numeric(df[tp_col], errors="coerce")
    s = pd.Series(v.values, index=d).dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s


def ts_iso(ts) -> str | None:
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def realized_vol(dgs10: pd.Series, window: int = 60) -> pd.Series:
    d = dgs10.dropna().diff()
    vol = d.rolling(window).std() * np.sqrt(252)
    return vol.dropna()


def recession_spans(usrec: pd.Series) -> list[dict[str, str]]:
    s = usrec.fillna(0).astype(float)
    spans, start, prev = [], None, None
    for dt, v in s.items():
        on = v >= 1
        if on and start is None:
            start = dt
        if not on and start is not None:
            spans.append({"start": start.strftime("%Y-%m-%d"), "end": prev.strftime("%Y-%m-%d")})
            start = None
        prev = dt
    if start is not None and prev is not None:
        spans.append({"start": start.strftime("%Y-%m-%d"), "end": prev.strftime("%Y-%m-%d")})
    return spans


def hist_pack(s: pd.Series | None, start="2000-01-01") -> list[dict]:
    if s is None or s.dropna().empty:
        return []
    return [
        {"d": ts_iso(i), "v": float(v)}
        for i, v in s.dropna().items()
        if pd.Timestamp(i) >= pd.Timestamp(start)
    ]


def window_return(px: pd.Series, start: str, end: str) -> float | None:
    s = px.dropna()
    if s.empty:
        return None
    a = s[s.index >= start]
    b = s[s.index <= end]
    if a.empty or b.empty:
        return None
    p0, p1 = float(a.iloc[0]), float(b.iloc[-1])
    if p0 == 0:
        return None
    return (p1 / p0 - 1.0) * 100.0


def collect() -> dict[str, pd.Series]:
    log("FRED 금리·BEI·정책")
    dgs10 = fred("DGS10")
    dgs2 = fred("DGS2")
    dgs30 = fred("DGS30")
    dfii10 = fred("DFII10")
    t10yie = fred("T10YIE")
    fedfunds = fred("FEDFUNDS")
    t10y3m = fred("T10Y3M")
    log("NY Fed ACM xls + FRED Kim-Wright")
    acm = pd.Series(dtype="float64")
    try:
        acm = parse_acm_xls(cached_get("ACMTermPremium.xls", ACM_XLS))
        log("ACM daily points", len(acm), "last", acm.index.max().date() if len(acm) else None)
    except Exception as e:
        log("ACM miss", e)
    kw = fred("THREEFYTP10")
    log("FRED 재정·신용·주가")
    deficit = fiscal_year_end(fred("FYFSGDA188S"))  # surplus (+)/deficit (-) % GDP
    debt = fred("GFDEBTN")  # millions, quarterly
    hy = fred("BAMLH0A0HYM2")
    spx = fred("SP500")
    nasdaq = fred("NASDAQCOM")
    ig = fred("BAMLC0A0CM")
    vix = fred("VIXCLS")
    usd = fred("DTWEXBGS")
    # FRED GOLDAMGBD228NLBM currently returns 404; do not substitute an unofficial feed.
    gold = pd.Series(dtype="float64")
    usrec = fred("USREC")
    interest_outlays = fiscal_year_end(fred("FYOINT"))
    federal_receipts = fiscal_year_end(fred("FYFR"))
    interest_gdp = fiscal_year_end(fred("FYOIGDA188S"))
    debt_gdp = fred("GFDEGDQ188S")
    interest_revenue = (interest_outlays / federal_receipts * 100.0).dropna()

    debt_held_public = pd.Series(dtype="float64")
    auction_df = pd.DataFrame()
    auctions = {"tenors": [], "quality": "MISSING"}
    issuance = pd.DataFrame()
    auction_stress = pd.Series(dtype="float64")
    try:
        log("Treasury Fiscal Data debt held by public")
        debt_rows, _ = fiscal_rows(
            "v2/accounting/od/debt_to_penny",
            fields="record_date,debt_held_public_amt,intragov_hold_amt,tot_pub_debt_out_amt",
            filter_expr="record_date:gte:1997-01-01",
            sort="record_date",
            page_size=10000,
            cache_name="debt_to_penny.json",
        )
        debt_held_public = debt_held_public_series(debt_rows)
    except Exception as e:
        log("Fiscal Data debt miss", e)
    try:
        log("Treasury Fiscal Data auctions")
        auction_rows, _ = fiscal_rows(
            "v1/accounting/od/auctions_query",
            fields=AUCTION_FIELDS,
            filter_expr="auction_date:gte:2008-04-01",
            sort="auction_date",
            page_size=10000,
            cache_name="auctions_since_2008.json",
        )
        auction_df = parse_auction_rows(auction_rows)
        today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
        completed_auctions = auction_df[auction_df["auction_date"] <= today].copy()
        issued_auctions = completed_auctions[completed_auctions["issue_date"] <= today].copy()
        auctions = auction_metrics(completed_auctions)
        issuance = issuance_monthly(issued_auctions)
        current_month_end = today.to_period("M").to_timestamp("M")
        issuance = issuance[issuance.index < current_month_end]
        auction_stress = auction_stress_history(completed_auctions)
    except Exception as e:
        log("Fiscal Data auction miss", e)
    if deficit.dropna().empty:
        log("FYFSGDA188S empty")
    deficit_stress = (-deficit).dropna()  # larger deficit = higher
    debt_yoy = debt.pct_change(4) * 100.0 if not debt.empty else pd.Series(dtype="float64")
    debt_held_public_yoy = debt_held_public.pct_change(252) * 100.0 if not debt_held_public.empty else pd.Series(dtype="float64")
    issuance_yoy = issuance["gross_accepted"].pct_change(12) * 100.0 if not issuance.empty else pd.Series(dtype="float64")
    coupon_issuance_yoy = issuance["coupon_total"].pct_change(12) * 100.0 if not issuance.empty else pd.Series(dtype="float64")
    long_duration_yoy = issuance["long_duration"].pct_change(12) * 100.0 if not issuance.empty else pd.Series(dtype="float64")
    vol = realized_vol(dgs10) if not dgs10.empty else pd.Series(dtype="float64")
    return {
        "dgs10": dgs10,
        "dgs2": dgs2,
        "dgs30": dgs30,
        "spread_2y10y": (dgs10 - dgs2).dropna(),
        "spread_10y30y": (dgs30 - dgs10).dropna(),
        "policy_path_proxy": (dgs2 - fedfunds.reindex(dgs2.index, method="ffill")).dropna(),
        "dfii10": dfii10,
        "t10yie": t10yie,
        "fedfunds": fedfunds,
        "acm_tp10": acm,
        "three_month_tp": t10y3m,
        "kim_wright_tp": kw,
        "federal_deficit": deficit_stress,
        "marketable_debt": debt_held_public_yoy if not debt_held_public_yoy.empty else debt_yoy,
        "debt_held_public": debt_held_public,
        "debt_gdp": debt_gdp,
        "interest_outlays": interest_outlays,
        "interest_revenue_pct": interest_revenue,
        "interest_gdp_pct": interest_gdp,
        "issuance_yoy": issuance_yoy,
        "coupon_issuance_yoy": coupon_issuance_yoy,
        "long_duration_yoy": long_duration_yoy,
        "issuance_table": issuance,
        "auction_table": auction_df,
        "auction_metrics": auctions,
        "auction_stress": auction_stress,
        "foreign_demand": pd.Series(dtype="float64"),
        "move_proxy": vol,
        "hy_oas": hy,
        "spx": spx,
        "nasdaq": nasdaq,
        "ig_oas": ig,
        "vix": vix,
        "usd": usd,
        "gold": gold,
        "usrec": usrec,
        "deficit_raw": deficit,
        "debt_level": debt,
    }


def scoring_series(spec, raw: dict[str, pd.Series]) -> pd.Series:
    s = raw.get(spec.key)
    if s is None:
        return pd.Series(dtype="float64")
    if spec.frequency == "daily":
        return month_end(s)
    if spec.frequency == "quarterly":
        return s.dropna().resample("MS").ffill()
    if spec.frequency in {"annual", "publication"}:
        return s.dropna()
    return s.dropna()


def build_payload(raw: dict[str, pd.Series], now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    scored = {}
    kpis = []
    for spec in KPI_SPECS:
        series = scoring_series(spec, raw)
        quality, note = classify_quality(spec, series, now, now)
        df = None
        if quality not in {"MISSING", "SOURCE_ERROR"} and not series.dropna().empty:
            df = score_series(spec, series)
            scored[spec.key] = df
        row = latest_row(df)
        latest_dt = None if series.dropna().empty else series.dropna().index.max()
        native = raw.get(spec.key)
        kpis.append(
            {
                "key": spec.key,
                "layer": spec.layer,
                "weight": spec.weight,
                "label_ko": spec.label_ko,
                "label_en": spec.label_en,
                "unit": spec.unit,
                "source": spec.source,
                "source_url": spec.source_url,
                "series_id": spec.series_id,
                "methodology": spec.methodology,
                "interpretation": spec.interpretation,
                "higher_is_stress": spec.higher_is_stress,
                "frequency": spec.frequency,
                "primary_model": spec.primary_model,
                "quality": quality,
                "quality_note": note,
                "latest_period": ts_iso(latest_dt),
                "latest_value": None if row is None else _num(row.get("value")),
                "previous_value": None if row is None else _num(row.get("previous")),
                "update_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "historical_range": {
                    "start": None if series.dropna().empty else ts_iso(series.dropna().index.min()),
                    "end": None if series.dropna().empty else ts_iso(series.dropna().index.max()),
                    "observations": int(series.dropna().shape[0]),
                },
                "notes": note,
                "change": None if row is None else _num(row.get("change")),
                "chg_1w": None if row is None else _num(row.get("chg_1w")),
                "chg_1m": None if row is None else _num(row.get("chg_1m")),
                "chg_3m": None if row is None else _num(row.get("chg_3m")),
                "chg_6m": None if row is None else _num(row.get("chg_6m")),
                "chg_yoy": None if row is None else _num(row.get("chg_yoy")),
                "level": None if row is None else _num(row.get("level")),
                "momentum": None if row is None else _num(row.get("momentum")),
                "acceleration": None if row is None else _num(row.get("acceleration")),
                "score": None if row is None else _num(row.get("score")),
                "history": hist_pack(month_end(native) if spec.frequency == "daily" and native is not None else series),
            }
        )

    acm = raw.get("acm_tp10", pd.Series(dtype=float))
    kw = raw.get("kim_wright_tp", pd.Series(dtype=float))
    curve_scores = [
        latest_stress_score(raw.get("spread_2y10y"), momentum_lag=63),
        latest_stress_score(raw.get("spread_10y30y"), momentum_lag=63),
    ]
    curve_score = _mean_present(curve_scores)
    rate_components = {
        "yield_level": latest_stress_score(raw.get("dgs10"), momentum_lag=63),
        "real_yield": latest_stress_score(raw.get("dfii10"), momentum_lag=63),
        "yield_momentum": latest_stress_score(raw.get("dgs10", pd.Series(dtype=float)).diff(63), momentum_lag=21),
        "curve_structure": curve_score,
        "fed_expectation": latest_stress_score(raw.get("policy_path_proxy"), momentum_lag=63),
    }
    dispersion_series = pd.Series(dtype="float64")
    if not acm.empty and not kw.empty:
        pair = pd.concat([acm.rename("acm"), kw.rename("kw")], axis=1, sort=True).dropna()
        if not pair.empty:
            dispersion_series = (pair["acm"] - pair["kw"]).abs()
    tp_accel_series = (acm - acm.shift(20)) - (acm.shift(20) - acm.shift(40)) if not acm.empty else pd.Series(dtype=float)
    term_components = {
        "level": latest_stress_score(acm, momentum_lag=63),
        "change_3m": latest_stress_score(acm.diff(63), momentum_lag=21),
        "acceleration": latest_stress_score(tp_accel_series, momentum_lag=20),
        "move": latest_stress_score(raw.get("move_proxy"), momentum_lag=20),
        "model_dispersion": latest_stress_score(dispersion_series, momentum_lag=63),
    }
    issuance_component = _mean_present([
        latest_stress_score(raw.get("issuance_yoy"), momentum_lag=3),
        latest_stress_score(raw.get("coupon_issuance_yoy"), momentum_lag=3),
        latest_stress_score(raw.get("long_duration_yoy"), momentum_lag=3),
    ])
    auction_series = raw.get("auction_stress", pd.Series(dtype=float))
    auction_component = None if auction_series is None or auction_series.dropna().empty else float(auction_series.dropna().iloc[-1])
    fiscal_components = {
        "deficit_gdp": latest_stress_score(raw.get("federal_deficit"), momentum_lag=1),
        "interest_burden": latest_stress_score(raw.get("interest_revenue_pct"), momentum_lag=1),
        "issuance_pressure": issuance_component,
        "auction_stress": auction_component,
        "foreign_demand": latest_stress_score(raw.get("foreign_demand"), higher_is_stress=False, momentum_lag=12),
    }
    sub_scores = {
        "rate": weighted_component_score(rate_components, CONFIG["subscore_weights"]["rate"]),
        "term_premium": weighted_component_score(term_components, CONFIG["subscore_weights"]["term_premium"]),
        "fiscal": weighted_component_score(fiscal_components, CONFIG["subscore_weights"]["fiscal"]),
    }

    hist = combine_scores(scored)
    rate_base = sub_scores["rate"]["score"]
    tp_base = sub_scores["term_premium"]["score"]
    fiscal_base = sub_scores["fiscal"]["score"]
    trrs = None
    if rate_base is not None and tp_base is not None and fiscal_base is not None:
        fw = CONFIG["final_weights"]
        trrs = float(rate_base * fw["rate"] + tp_base * fw["term_premium"] + fiscal_base * fw["fiscal"])
    rs = rate_base
    tps = tp_base
    fs = fiscal_base
    latest_dt = None if hist.empty or hist["trrs"].dropna().empty else hist["trrs"].dropna().index.max()
    acm = raw.get("acm_tp10", pd.Series(dtype=float))
    kw = raw.get("kim_wright_tp", pd.Series(dtype=float))
    acm_last = None if acm.dropna().empty else float(acm.dropna().iloc[-1])
    kw_last = None if kw.dropna().empty else float(kw.dropna().iloc[-1])
    disp = model_dispersion({"ACM": acm_last, "Kim-Wright": kw_last})
    tp_monthly = month_end(acm) if not acm.empty else pd.Series(dtype=float)
    tp_state = tp_acceleration_state(tp_monthly, lookback=3)
    tp_slopes = term_premium_slopes(acm)
    tp_z = rolling_zscore(acm, window=252, min_periods=60) if not acm.empty else pd.Series(dtype=float)
    tp_change_63 = acm - acm.shift(63) if not acm.empty else pd.Series(dtype=float)
    tp_change_pct = None
    if not tp_change_63.dropna().empty:
        arr = tp_change_63.dropna().to_numpy(dtype="float64")
        tp_change_pct = expanding_percentile(arr, float(arr[-1]), False)
    override = apply_acceleration_override(trrs, basis_points(tp_state.get("chg_3m")), tp_change_pct, CONFIG)
    trrs = override["score"]
    dgs_m = month_end(raw["dgs10"])
    real_m = month_end(raw["dfii10"])
    bei_m = month_end(raw["t10yie"])
    decomp = decompose_nominal(dgs_m, real_m, bei_m, expected_short=dgs_m - month_end(acm) if not acm.empty else None, term_premium=month_end(acm) if not acm.empty else None)
    expected_short_daily = pd.Series(dtype="float64")
    if not raw["dgs10"].empty and not acm.empty:
        aligned = pd.concat([raw["dgs10"].rename("nominal"), acm.rename("tp")], axis=1, sort=True).dropna()
        if not aligned.empty:
            expected_short_daily = aligned["nominal"] - aligned["tp"]
    real_chg = latest_change(real_m, 3)
    bei_chg = latest_change(bei_m, 3)
    tp_chg = tp_state.get("chg_3m")
    regime = classify_regime(real_chg, bei_chg, tp_chg, bool(tp_state.get("acceleration")), fs)
    n_ok = sum(1 for k in kpis if k["score"] is not None)
    conf = "HIGH"
    if n_ok < 8:
        conf = "MEDIUM" if n_ok >= 6 else "LOW"
    if any(k["quality"] in {"STALE", "MISSING", "SOURCE_ERROR", "METHODOLOGY_CHANGED"} for k in kpis) and conf == "HIGH":
        conf = "MEDIUM"
    if any(item["quality"] != "VALID" for item in sub_scores.values()) and conf == "HIGH":
        conf = "MEDIUM"
    if hist.empty or trrs is None:
        conf = "LOW"

    why = _why_rising(decomp, tp_state, regime, raw)
    equity = _equity_windows(raw)
    backtest = backtest_events(acm, raw.get("spx", pd.Series(dtype=float)), raw.get("nasdaq", pd.Series(dtype=float)), start="2000-01-01", config=CONFIG)
    rate_vol_series = raw.get("move_proxy", pd.Series(dtype=float))
    rate_vol_rising = None if len(rate_vol_series.dropna()) <= 20 else bool(rate_vol_series.dropna().iloc[-1] > rate_vol_series.dropna().iloc[-21])
    alerts = evaluate_alerts(
        tp_level_pct=acm_last,
        tp_change_3m_bp=basis_points(tp_state.get("chg_3m")),
        real_yield_pct=_last(raw.get("dfii10")),
        rate_vol_rising=rate_vol_rising,
        auction_deteriorating=_auction_deteriorating(raw.get("auction_metrics")),
        equity_return_1m_pct=_trailing_return(raw.get("spx"), 21),
        treasury_price_return_1m_pct=None,
    )
    interp = {
        "why_yields": why["headline"],
        "fed_vs_inflation_vs_real_vs_tp": why["split"],
        "term_premium_structure": tp_state["state"],
        "fiscal_supply": "적자·부채 증가가 스트레스 구간에 있으면 공급 압력으로 읽는다." if (fs or 0) >= 60 else "재정 스트레스는 아직 주도 축이 아니다.",
        "acceleration": "가속" if tp_state.get("acceleration") else tp_state.get("state"),
        "equity_ai": "국채 스트레스가 신용·변동성으로 번지면 AI 캡엑스 할인율에 영향을 줄 수 있다. 캡엑스 자체 공식 월간 시계열은 이 보드에 넣지 않았다.",
        "regime": regime["regime"],
        "regime_note": regime["note"],
        "overall": status_label(trrs),
    }

    payload = {
        "meta": {
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "as_of": ts_iso(raw["dgs10"].dropna().index.max()) if not raw["dgs10"].dropna().empty else ts_iso(latest_dt),
            "as_of_label": ts_iso(raw["dgs10"].dropna().index.max()) if not raw["dgs10"].dropna().empty else None,
            "data_confidence": conf,
            "weight_sum": 1.0,
            "primary_term_premium": "ACM ACMTP10",
        },
        "hero": {
            "trrs": trrs,
            "rate_stress": rs,
            "tp_stress": tps,
            "fiscal_stress": fs,
            "status": status_label(trrs),
            "color": color_band(trrs),
            "regime": regime,
            "tp_state": tp_state,
            "tp_slopes": tp_slopes,
            "tp_zscore_1y": None if tp_z.dropna().empty else float(tp_z.dropna().iloc[-1]),
            "tp_change_3m_percentile": tp_change_pct,
            "acceleration_override": override,
            "dispersion": disp,
            "confidence": conf,
        },
        "kpis": kpis,
        "sub_scores": {
            "rate": {**sub_scores["rate"], "components": rate_components},
            "term_premium": {**sub_scores["term_premium"], "components": term_components},
            "fiscal": {**sub_scores["fiscal"], "components": fiscal_components},
        },
        "layers": {
            "rate": rs,
            "term_premium": tps,
            "fiscal": fs,
            "transmission": None if hist.empty else _num(hist["transmission_stress"].dropna().iloc[-1] if not hist["transmission_stress"].dropna().empty else None),
        },
        "fiscal_supply": {
            "budget_deficit_gdp": _last(raw.get("federal_deficit")),
            "debt_held_public_usd": _last(raw.get("debt_held_public")),
            "debt_held_public_yoy_pct": _last(raw.get("marketable_debt")),
            "debt_gdp_pct": _last(raw.get("debt_gdp")),
            "interest_outlays_musd": _last(raw.get("interest_outlays")),
            "interest_revenue_pct": _last(raw.get("interest_revenue_pct")),
            "interest_gdp_pct": _last(raw.get("interest_gdp_pct")),
            "gross_issuance_yoy_pct": _last(raw.get("issuance_yoy")),
            "coupon_issuance_yoy_pct": _last(raw.get("coupon_issuance_yoy")),
            "long_duration_issuance_yoy_pct": _last(raw.get("long_duration_yoy")),
            "latest_issuance": _latest_issuance(raw.get("issuance_table")),
            "foreign_treasury_holdings": None,
            "foreign_share": None,
            "primary_dealer_holdings": None,
            "quality": "PARTIAL",
            "notes": "TIC foreign holdings and NY Fed primary-dealer positions are not yet in the score. Net marketable borrowing has no stable Fiscal Data API and is not fabricated.",
        },
        "auction": raw.get("auction_metrics", {"tenors": [], "quality": "MISSING"}),
        "alerts": alerts,
        "rate_structure": {
            "us_2y": _last(raw.get("dgs2")),
            "us_10y": _last(raw.get("dgs10")),
            "us_30y": _last(raw.get("dgs30")),
            "spread_2y10y": _last(raw.get("spread_2y10y")),
            "spread_10y30y": _last(raw.get("spread_10y30y")),
            "real_10y": _last(raw.get("dfii10")),
            "breakeven_10y": _last(raw.get("t10yie")),
            "expected_short": None if acm_last is None or dgs_m.dropna().empty else float(dgs_m.dropna().iloc[-1] - acm_last),
            "effective_fed_funds": _last(raw.get("fedfunds")),
            "fed_policy_expectation": _last(raw.get("policy_path_proxy")),
            "fed_policy_expectation_label": "2Y minus effective fed funds proxy (not Fed futures)",
        },
        "term_premium_analytics": {
            "level": acm_last,
            "change_1m_bp": basis_points(latest_change(tp_monthly, 1)),
            "change_3m_bp": basis_points(tp_state.get("chg_3m")),
            "change_6m_bp": basis_points(tp_state.get("chg_6m")),
            "acceleration": tp_state,
            "slopes": tp_slopes,
            "rolling_zscore_1y": None if tp_z.dropna().empty else float(tp_z.dropna().iloc[-1]),
            "historical_percentile": None if acm.dropna().empty else expanding_percentile(acm.dropna().to_numpy(), acm_last, False),
            "change_3m_percentile": tp_change_pct,
            "zone": _tp_zone(acm_last),
            "model_dispersion": disp,
        },
        "decomposition": {
            "nominal": None if dgs_m.dropna().empty else float(dgs_m.dropna().iloc[-1]),
            "real": None if real_m.dropna().empty else float(real_m.dropna().iloc[-1]),
            "bei": None if bei_m.dropna().empty else float(bei_m.dropna().iloc[-1]),
            "acm_tp": acm_last,
            "kim_wright_tp": kw_last,
            "expected_short": None if acm_last is None or dgs_m.dropna().empty else float(dgs_m.dropna().iloc[-1] - acm_last),
            "chg_3m": {
                "nominal": latest_change(dgs_m, 3),
                "real": real_chg,
                "bei": bei_chg,
                "acm_tp": tp_chg,
            },
        },
        "interpretation": interp,
        "equity_windows": equity,
        "backtest": backtest,
        "config_public": {
            "final_weights": CONFIG["final_weights"],
            "risk_bands": CONFIG["risk_bands"],
            "term_premium_zones_pct": CONFIG["term_premium_zones_pct"],
            "acceleration_override": CONFIG["acceleration_override"],
        },
        "charts": {
            "trrs": hist_pack(hist["trrs"] if not hist.empty else None),
            "dgs10": hist_pack(month_end(raw["dgs10"])),
            "expected_short": hist_pack(month_end(expected_short_daily)),
            "dgs2": hist_pack(month_end(raw["dgs2"])),
            "dgs30": hist_pack(month_end(raw["dgs30"])),
            "spread_2y10y": hist_pack(month_end(raw["spread_2y10y"])),
            "spread_10y30y": hist_pack(month_end(raw["spread_10y30y"])),
            "real": hist_pack(month_end(raw["dfii10"])),
            "bei": hist_pack(month_end(raw["t10yie"])),
            "acm": hist_pack(month_end(raw["acm_tp10"])),
            "kim_wright": hist_pack(month_end(raw["kim_wright_tp"])),
            "hy": hist_pack(month_end(raw["hy_oas"])),
            "spx": hist_pack(month_end(raw["spx"])),
            "nasdaq": hist_pack(month_end(raw["nasdaq"])),
            "ig": hist_pack(month_end(raw["ig_oas"])),
            "vix": hist_pack(month_end(raw["vix"])),
            "usd": hist_pack(month_end(raw["usd"])),
            "gold": hist_pack(month_end(raw["gold"])),
            "recessions": recession_spans(raw["usrec"]) if "usrec" in raw and not raw["usrec"].empty else [],
        },
        "notes": {
            "term_premium": "텀프리미엄은 관측값이 아니라 모델 추정치다. 대표 모델은 NY Fed ACM이며 Kim-Wright(THREEFYTP10)를 병행한다. 없는 모델은 만들지 않는다.",
            "ai_capex": "하이퍼스케일러 CapEx는 분기 공시라 이 금리 보드의 공식 월간 KPI에 넣지 않았다. 전이는 하이일드 스프레드·금리 변동성으로만 본다.",
            "no_estimates": "결측은 공란이다. 뉴스·IB 리포트는 원자료가 아니다.",
        },
    }
    return payload


def _num(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if pd.isna(v):
        return None
    return v


def _last(series: pd.Series | None) -> float | None:
    if series is None or series.dropna().empty:
        return None
    return float(series.dropna().iloc[-1])


def _mean_present(values: list[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None and np.isfinite(v)]
    return None if not present else float(np.mean(present))


def _latest_issuance(frame: pd.DataFrame | None) -> dict[str, Any] | None:
    if frame is None or frame.empty:
        return None
    row = frame.iloc[-1]
    return {
        "period": ts_iso(frame.index[-1]),
        "gross_accepted_usd": _num(row.get("gross_accepted")),
        "bills_usd": _num(row.get("bills_total")),
        "coupon_usd": _num(row.get("coupon_total")),
        "long_duration_usd": _num(row.get("long_duration")),
        "long_share_pct": _num(row.get("long_share_pct")),
        "weighted_average_maturity_years": _num(row.get("weighted_average_maturity_years")),
    }


def _trailing_return(series: pd.Series | None, periods: int) -> float | None:
    if series is None:
        return None
    s = series.dropna()
    if len(s) <= periods or float(s.iloc[-1 - periods]) == 0:
        return None
    return float((s.iloc[-1] / s.iloc[-1 - periods] - 1.0) * 100.0)


def _auction_deteriorating(metrics: dict | None) -> bool | None:
    if not metrics or not metrics.get("tenors"):
        return None
    flags = []
    for item in metrics["tenors"]:
        r3, r6 = item.get("rolling_3") or {}, item.get("rolling_6") or {}
        if not r3.get("complete") or not r6.get("complete"):
            continue
        flags.append(
            (r3.get("bid_to_cover") or 0) < (r6.get("bid_to_cover") or 0)
            and (r3.get("dealer_pct") or 0) > (r6.get("dealer_pct") or 0)
        )
    return None if not flags else bool(sum(flags) >= max(2, len(flags) // 2 + 1))


def _tp_zone(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "UNAVAILABLE"
    for row in CONFIG["term_premium_zones_pct"]:
        if float(row["min"]) <= float(value) < float(row["max"]):
            return str(row["label"])
    return "UNAVAILABLE"


def _why_rising(decomp: pd.DataFrame, tp_state: dict, regime: dict, raw: dict) -> dict:
    chg = {}
    if not decomp.empty and "nominal" in decomp:
        nom = decomp["nominal"].dropna()
        chg["nominal"] = latest_change(nom, 3)
    headline = regime.get("note") or "원인 축이 한쪽으로 기울지 않았다."
    if (chg.get("nominal") or 0) <= 0:
        headline = "최근 3개월 명목 10년물은 상승 주도가 아니다. 레벨과 구성만 확인한다."
    split = (
        f"3M Δ 실질 {fmt_bp(latest_change(month_end(raw['dfii10']), 3))} · "
        f"BEI {fmt_bp(latest_change(month_end(raw['t10yie']), 3))} · "
        f"ACM TP {fmt_bp(tp_state.get('chg_3m'))}"
    )
    return {"headline": headline, "split": split}


def fmt_bp(v):
    if v is None or not np.isfinite(v):
        return "—"
    return f"{v*100:+.0f}bp"


def _equity_windows(raw: dict) -> list[dict]:
    spx, nasdaq = raw.get("spx"), raw.get("nasdaq")
    windows = [
        ("2013 taper", "2013-05-01", "2013-09-30"),
        ("2023 term-premium / banking", "2023-02-01", "2023-10-31"),
        ("2022 inflation-hike", "2022-01-01", "2022-10-31"),
    ]
    out = []
    for name, a, b in windows:
        out.append(
            {
                "window": name,
                "start": a,
                "end": b,
                "spx_pct": None if spx is None else window_return(spx, a, b),
                "nasdaq_pct": None if nasdaq is None else window_return(nasdaq, a, b),
            }
        )
    return out


def render_html(payload: dict, template: str) -> str:
    blob = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if "__DATA_JSON__" not in template:
        raise RuntimeError("template missing __DATA_JSON__")
    return template.replace("__DATA_JSON__", blob)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "dist"))
    p.add_argument("--template", default=str(ROOT / "template.html"))
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = collect()
    payload = build_payload(raw)
    (out / "trrs_data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(payload, Path(args.template).read_text(encoding="utf-8"))
    (out / "index.html").write_text(html, encoding="utf-8")
    trrs = payload["hero"]["trrs"]
    log(
        "완료 — TRRS",
        "None" if trrs is None else f"{trrs:.1f}",
        payload["hero"]["status"],
        payload["meta"]["data_confidence"],
        payload["hero"]["regime"]["regime"],
        payload["meta"]["as_of_label"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
