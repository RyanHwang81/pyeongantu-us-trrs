#!/usr/bin/env python3
"""Official U.S. Treasury Fiscal Data collectors and deterministic transforms."""
from __future__ import annotations

import json
import math
import os
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
UA = {"User-Agent": "Mozilla/5.0 (compatible; pyeongantoo-trrs-builder/1.0)"}
CACHE = os.environ.get("TRRS_CACHE", "")
TARGET_TENORS = ("2-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year")
AUCTION_FIELDS = (
    "auction_date,issue_date,maturity_date,cusip,security_type,security_term,"
    "original_security_term,inflation_index_security,floating_rate,reopening,"
    "high_yield,bid_to_cover_ratio,direct_bidder_accepted,indirect_bidder_accepted,"
    "primary_dealer_accepted,comp_accepted,comp_tendered,total_accepted,total_tendered,"
    "offering_amt,record_date,pdf_filenm_comp_results,xml_filenm_comp_results"
)

try:
    import certifi

    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    CTX = ssl.create_default_context()


def safe_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "nan", "*"}:
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fetch_json(url: str, cache_name: str | None = None, tries: int = 4) -> dict[str, Any]:
    if cache_name and CACHE:
        path = Path(CACHE) / cache_name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX, timeout=60) as response:
                payload = json.load(response)
            if cache_name and CACHE:
                path = Path(CACHE) / cache_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
            return payload
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Fiscal Data download failed: {url} ({last})")


def fiscal_rows(
    endpoint: str,
    *,
    fields: str,
    filter_expr: str | None = None,
    sort: str = "record_date",
    page_size: int = 10000,
    cache_name: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    params = {"fields": fields, "sort": sort, "page[size]": str(page_size), "page[number]": "1"}
    if filter_expr:
        params["filter"] = filter_expr
    base_url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(params, safe=',:()')}"
    first = _fetch_json(base_url, cache_name=cache_name)
    data = list(first.get("data") or [])
    meta = dict(first.get("meta") or {})
    total_pages = int(meta.get("total-pages") or 1)
    for page in range(2, total_pages + 1):
        params["page[number]"] = str(page)
        url = f"{BASE}/{endpoint}?{urllib.parse.urlencode(params, safe=',:()')}"
        payload = _fetch_json(url)
        data.extend(payload.get("data") or [])
    total_count = int(meta.get("total-count") or len(data))
    if len(data) != total_count:
        raise RuntimeError(f"Fiscal Data pagination mismatch: expected {total_count}, got {len(data)}")
    return data, meta


def debt_held_public_series(rows: list[dict[str, Any]]) -> pd.Series:
    pairs = []
    for row in rows:
        date = pd.to_datetime(row.get("record_date"), errors="coerce")
        value = safe_number(row.get("debt_held_public_amt"))
        if pd.isna(date) or value is None:
            continue
        pairs.append((date, value))
    if not pairs:
        return pd.Series(dtype="float64")
    s = pd.Series({d: v for d, v in pairs}, dtype="float64").sort_index()
    return s[~s.index.duplicated(keep="last")]


def parse_auction_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records = []
    numeric = [
        "high_yield",
        "bid_to_cover_ratio",
        "direct_bidder_accepted",
        "indirect_bidder_accepted",
        "primary_dealer_accepted",
        "comp_accepted",
        "comp_tendered",
        "total_accepted",
        "total_tendered",
        "offering_amt",
    ]
    for row in rows:
        tenor = str(row.get("original_security_term") or "").strip()
        if str(row.get("inflation_index_security") or "").lower() == "yes":
            continue
        if str(row.get("floating_rate") or "").lower() == "yes":
            continue
        rec = dict(row)
        for col in ("auction_date", "issue_date", "maturity_date", "record_date"):
            rec[col] = pd.to_datetime(rec.get(col), errors="coerce")
        for col in numeric:
            rec[col] = safe_number(rec.get(col))
        comp = rec.get("comp_accepted")
        for source, target in [
            ("direct_bidder_accepted", "direct_pct"),
            ("indirect_bidder_accepted", "indirect_pct"),
            ("primary_dealer_accepted", "dealer_pct"),
        ]:
            numerator = rec.get(source)
            rec[target] = None if comp in {None, 0} or numerator is None else numerator / comp * 100.0
        # No official/free stable when-issued series: never fabricate tail.
        rec["wi_yield_pre_auction"] = None
        rec["auction_tail_bp"] = None
        rec["tail_status"] = "official_free_source_unavailable"
        rec["tenor"] = tenor
        records.append(rec)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).sort_values(["tenor", "auction_date", "issue_date"])
    return df.reset_index(drop=True)


def _avg_dict(frame: pd.DataFrame, n: int) -> dict[str, Any]:
    valid = frame.dropna(subset=["high_yield", "bid_to_cover_ratio", "comp_accepted", "direct_pct", "indirect_pct", "dealer_pct"])
    if len(valid) < n:
        return {"n": int(len(valid)), "complete": False}
    tail = valid.tail(n)
    return {
        "n": n,
        "complete": True,
        "high_yield": float(tail["high_yield"].mean()),
        "bid_to_cover": float(tail["bid_to_cover_ratio"].mean()),
        "direct_pct": float(tail["direct_pct"].mean()),
        "indirect_pct": float(tail["indirect_pct"].mean()),
        "dealer_pct": float(tail["dealer_pct"].mean()),
    }


def auction_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"tenors": [], "latest_date": None, "quality": "MISSING"}
    tenors = []
    for tenor in TARGET_TENORS:
        sub = df[df["tenor"] == tenor].sort_values("auction_date")
        complete = sub.dropna(subset=["high_yield", "bid_to_cover_ratio", "comp_accepted", "direct_pct", "indirect_pct", "dealer_pct"])
        if complete.empty:
            latest = {
                "auction_date": None,
                "high_yield": None,
                "bid_to_cover": None,
                "direct_pct": None,
                "indirect_pct": None,
                "dealer_pct": None,
                "auction_tail_bp": None,
                "tail_status": "official_free_source_unavailable",
            }
        else:
            row = complete.iloc[-1]
            latest = {
                "auction_date": row["auction_date"].strftime("%Y-%m-%d"),
                "high_yield": float(row["high_yield"]),
                "bid_to_cover": float(row["bid_to_cover_ratio"]),
                "direct_pct": float(row["direct_pct"]),
                "indirect_pct": float(row["indirect_pct"]),
                "dealer_pct": float(row["dealer_pct"]),
                "auction_tail_bp": None,
                "tail_status": "official_free_source_unavailable",
            }
        tenors.append({"tenor": tenor, "latest": latest, "rolling_3": _avg_dict(sub, 3), "rolling_6": _avg_dict(sub, 6)})
    completed_dates = [pd.Timestamp(x["latest"]["auction_date"]) for x in tenors if x["latest"].get("auction_date")]
    return {
        "tenors": tenors,
        "latest_date": None if not completed_dates else max(completed_dates).strftime("%Y-%m-%d"),
        "quality": "VALID" if any(x["rolling_3"].get("complete") for x in tenors) else "MISSING",
        "tail_quality": "MISSING",
        "tail_note": "Official free when-issued yield is unavailable; auction tail is intentionally null.",
    }


def issuance_monthly(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    valid = df.dropna(subset=["issue_date", "total_accepted", "maturity_date"]).copy()
    if valid.empty:
        return pd.DataFrame()
    valid["month"] = valid["issue_date"].dt.to_period("M").dt.to_timestamp("M")
    valid["years_to_maturity"] = (valid["maturity_date"] - valid["issue_date"]).dt.days / 365.25
    valid["weighted_years"] = valid["total_accepted"] * valid["years_to_maturity"]
    valid["is_long"] = valid["tenor"].isin(["10-Year", "20-Year", "30-Year"])
    valid["is_bill"] = valid["security_type"].eq("Bill")
    valid["is_coupon"] = valid["security_type"].isin(["Note", "Bond"])
    rows = []
    for month, group in valid.groupby("month"):
        total = float(group["total_accepted"].sum())
        coupon = float(group.loc[group["is_coupon"], "total_accepted"].sum())
        bills = float(group.loc[group["is_bill"], "total_accepted"].sum())
        long_d = float(group.loc[group["is_long"], "total_accepted"].sum())
        wam = None if total <= 0 else float(group["weighted_years"].sum() / total)
        rows.append(
            {
                "date": month,
                "gross_accepted": total,
                "coupon_total": coupon,
                "bills_total": bills,
                "long_duration": long_d,
                "long_share_pct": None if coupon <= 0 else long_d / coupon * 100.0,
                "weighted_average_maturity_years": wam,
            }
        )
    return pd.DataFrame(rows).set_index("date").sort_index()


def auction_stress_history(df: pd.DataFrame) -> pd.Series:
    """Observable auction-demand composite; tail excluded because WI is unavailable."""
    if df is None or df.empty:
        return pd.Series(dtype="float64")
    valid = df.dropna(subset=["auction_date", "bid_to_cover_ratio", "indirect_pct", "dealer_pct"]).copy()
    if valid.empty:
        return pd.Series(dtype="float64")
    # rolling expanding ranks are calculated using data available at each auction
    valid = valid.sort_values("auction_date")
    scores = []
    for i, row in valid.reset_index(drop=True).iterrows():
        hist = valid.reset_index(drop=True).iloc[: i + 1]
        if len(hist) < 20:
            scores.append(np.nan)
            continue
        btc_stress = 100.0 - float((hist["bid_to_cover_ratio"] <= row["bid_to_cover_ratio"]).mean() * 100.0)
        indirect_stress = 100.0 - float((hist["indirect_pct"] <= row["indirect_pct"]).mean() * 100.0)
        dealer_stress = float((hist["dealer_pct"] <= row["dealer_pct"]).mean() * 100.0)
        scores.append(0.35 * btc_stress + 0.35 * indirect_stress + 0.30 * dealer_stress)
    valid["stress"] = scores
    return valid.dropna(subset=["stress"]).set_index("auction_date")["stress"].resample("ME").mean().dropna()
