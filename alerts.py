#!/usr/bin/env python3
"""Pure alert rules for Treasury regime monitoring."""
from __future__ import annotations

from typing import Any


def evaluate_alerts(
    *,
    tp_level_pct: float | None,
    tp_change_3m_bp: float | None,
    real_yield_pct: float | None,
    rate_vol_rising: bool | None,
    auction_deteriorating: bool | None,
    equity_return_1m_pct: float | None,
    treasury_price_return_1m_pct: float | None,
) -> list[dict[str, Any]]:
    alerts = []

    def add(key: str, label: str, active: bool, severity: str, evidence: str):
        alerts.append({"key": key, "label": label, "active": bool(active), "severity": severity, "evidence": evidence})

    level = tp_level_pct
    chg = tp_change_3m_bp
    add("tp_150", "10Y Term Premium > 1.50%", level is not None and level > 1.50, "WARNING", f"TP={level}" if level is not None else "MISSING")
    add("tp_175", "10Y Term Premium > 1.75%", level is not None and level > 1.75, "STRESS", f"TP={level}" if level is not None else "MISSING")
    add("tp_200", "10Y Term Premium > 2.00%", level is not None and level > 2.00, "SEVERE", f"TP={level}" if level is not None else "MISSING")
    add("tp_chg_50", "3M Term Premium change > +50bp", chg is not None and chg > 50, "WARNING", f"3M={chg}bp" if chg is not None else "MISSING")
    add(
        "tp_real_combo",
        "TP > 1.50% and real yield > 2.75%",
        level is not None and real_yield_pct is not None and level > 1.50 and real_yield_pct > 2.75,
        "STRESS",
        f"TP={level}, real={real_yield_pct}" if level is not None and real_yield_pct is not None else "MISSING",
    )
    add("tp_vol", "TP rising rapidly and rate volatility rising", chg is not None and chg > 30 and rate_vol_rising is True, "WARNING", f"3M={chg}bp, vol_rising={rate_vol_rising}")
    add("tp_auction", "TP stress and auction deterioration", level is not None and level > 1.25 and auction_deteriorating is True, "STRESS", f"TP={level}, auction_deteriorating={auction_deteriorating}")
    add(
        "stock_bond_down",
        "Equity down + Treasury prices down + TP rising",
        equity_return_1m_pct is not None
        and treasury_price_return_1m_pct is not None
        and chg is not None
        and equity_return_1m_pct < 0
        and treasury_price_return_1m_pct < 0
        and chg > 0,
        "FISCAL CONFIDENCE WATCH",
        f"equity={equity_return_1m_pct}, treasury={treasury_price_return_1m_pct}, TP3M={chg}" if equity_return_1m_pct is not None and treasury_price_return_1m_pct is not None and chg is not None else "MISSING",
    )
    return alerts
