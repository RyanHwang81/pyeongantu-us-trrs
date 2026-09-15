#!/usr/bin/env python3
import unittest
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from official_data import (  # noqa: E402
    auction_metrics,
    debt_held_public_series,
    issuance_monthly,
    parse_auction_rows,
    safe_number,
)


class TestFiscalParsing(unittest.TestCase):
    def test_safe_number(self):
        self.assertEqual(safe_number("123.45"), 123.45)
        self.assertIsNone(safe_number("null"))
        self.assertIsNone(safe_number(None))

    def test_debt_series(self):
        rows = [
            {"record_date": "2026-01-02", "debt_held_public_amt": "100"},
            {"record_date": "2026-01-03", "debt_held_public_amt": "110"},
        ]
        s = debt_held_public_series(rows)
        self.assertEqual(float(s.iloc[-1]), 110.0)
        self.assertEqual(s.index.max(), pd.Timestamp("2026-01-03"))


class TestAuctionData(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for i, tenor in enumerate(["2-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year"]):
            for j in range(6):
                self.rows.append(
                    {
                        "auction_date": f"2026-0{j+1}-{10+i:02d}",
                        "issue_date": f"2026-0{j+1}-{15+i:02d}",
                        "maturity_date": f"20{28+i*2}-0{j+1}-{15+i:02d}",
                        "cusip": f"CUSIP{i}{j}",
                        "security_type": "Note" if tenor not in {"20-Year", "30-Year"} else "Bond",
                        "security_term": tenor,
                        "original_security_term": tenor,
                        "inflation_index_security": "No",
                        "floating_rate": "No",
                        "reopening": "No",
                        "high_yield": str(4.0 + i * .1 + j * .01),
                        "bid_to_cover_ratio": str(2.5 - j * .02),
                        "direct_bidder_accepted": "10",
                        "indirect_bidder_accepted": "60",
                        "primary_dealer_accepted": "30",
                        "comp_accepted": "100",
                        "total_accepted": "105",
                        "offering_amt": "110",
                    }
                )

    def test_parse_and_bidder_shares(self):
        df = parse_auction_rows(self.rows)
        self.assertEqual(len(df), 36)
        self.assertAlmostEqual(df.iloc[0]["direct_pct"], 10.0)
        self.assertAlmostEqual(df.iloc[0]["indirect_pct"], 60.0)
        self.assertAlmostEqual(df.iloc[0]["dealer_pct"], 30.0)
        self.assertTrue(df["auction_tail_bp"].isna().all())

    def test_rolling_three_six_by_tenor(self):
        df = parse_auction_rows(self.rows)
        metrics = auction_metrics(df)
        row = next(x for x in metrics["tenors"] if x["tenor"] == "10-Year")
        self.assertEqual(row["rolling_3"]["n"], 3)
        self.assertEqual(row["rolling_6"]["n"], 6)
        self.assertIsNone(row["latest"]["auction_tail_bp"])
        self.assertEqual(row["latest"]["tail_status"], "official_free_source_unavailable")

    def test_issuance_and_long_duration(self):
        df = parse_auction_rows(self.rows)
        monthly = issuance_monthly(df)
        self.assertGreaterEqual(len(monthly), 6)
        self.assertIn("coupon_total", monthly.columns)
        self.assertIn("long_duration", monthly.columns)
        self.assertIn("weighted_average_maturity_years", monthly.columns)


if __name__ == "__main__":
    unittest.main()
