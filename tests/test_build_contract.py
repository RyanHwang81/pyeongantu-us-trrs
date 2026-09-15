#!/usr/bin/env python3
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

from build import fiscal_year_end, month_end


class TestTemplateContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "template.html").read_text(encoding="utf-8")

    def test_required_sections(self):
        for marker in [
            'id="top-kpis"',
            'id="regime-matrix"',
            'id="chart-short-tp"',
            'id="chart-decomp"',
            'id="chart-tp"',
            'id="fiscal-panel"',
            'id="auction-panel"',
            'id="backtest-panel"',
        ]:
            self.assertIn(marker, self.html)

    def test_dark_and_mobile(self):
        self.assertIn("prefers-color-scheme:dark", self.html)
        self.assertIn("max-width:390px", self.html)
        self.assertIn("table-scroll", self.html)

    def test_score_data_comes_from_payload(self):
        self.assertIn("DATA.hero", self.html)
        self.assertNotIn("const TRRS = 67", self.html)

    def test_iframe_height_contract(self):
        self.assertIn('type:"gl-height"', self.html)


class TestObservedDate(unittest.TestCase):
    def test_month_end_preserves_actual_last_observation_date(self):
        s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2026-08-31", "2026-09-01", "2026-09-11"]))
        out = month_end(s)
        self.assertEqual(out.index[-1], pd.Timestamp("2026-09-11"))
        self.assertEqual(float(out.iloc[-1]), 3.0)

    def test_fred_fiscal_year_label_becomes_sep_30(self):
        s = pd.Series([5.0], index=pd.to_datetime(["2025-01-01"]))
        out = fiscal_year_end(s)
        self.assertEqual(out.index[-1], pd.Timestamp("2025-09-30"))


class TestWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.yml = (ROOT / ".github/workflows/update.yml").read_text(encoding="utf-8")

    def test_schedule_and_manual(self):
        self.assertIn('cron: "0 9 1,16 * *"', self.yml)
        self.assertIn("workflow_dispatch:", self.yml)

    def test_runner_test_build_pages(self):
        self.assertIn("self-hosted, macOS, gl-monitor", self.yml)
        self.assertIn("unittest discover", self.yml)
        self.assertIn("build.py --out dist", self.yml)
        self.assertIn("actions/deploy-pages", self.yml)


if __name__ == "__main__":
    unittest.main()
