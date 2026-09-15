#!/usr/bin/env python3
import unittest

from alerts import evaluate_alerts


class TestAlerts(unittest.TestCase):
    def test_tp_level_alerts(self):
        rows = evaluate_alerts(tp_level_pct=1.8, tp_change_3m_bp=20, real_yield_pct=2.0, rate_vol_rising=False, auction_deteriorating=False, equity_return_1m_pct=1, treasury_price_return_1m_pct=-1)
        active = {x["key"] for x in rows if x["active"]}
        self.assertIn("tp_150", active)
        self.assertIn("tp_175", active)
        self.assertNotIn("tp_200", active)

    def test_fiscal_confidence_watch(self):
        rows = evaluate_alerts(tp_level_pct=1.0, tp_change_3m_bp=10, real_yield_pct=2.0, rate_vol_rising=False, auction_deteriorating=False, equity_return_1m_pct=-3, treasury_price_return_1m_pct=-2)
        active = {x["key"] for x in rows if x["active"]}
        self.assertIn("stock_bond_down", active)

    def test_missing_values_do_not_fire(self):
        rows = evaluate_alerts(tp_level_pct=None, tp_change_3m_bp=None, real_yield_pct=None, rate_vol_rising=None, auction_deteriorating=None, equity_return_1m_pct=None, treasury_price_return_1m_pct=None)
        self.assertFalse(any(x["active"] for x in rows))


if __name__ == "__main__":
    unittest.main()
