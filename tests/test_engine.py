#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    CONFIG,
    KPI_SPECS,
    WEIGHT_SUM,
    apply_acceleration_override,
    backtest_events,
    basis_points,
    classify_regime,
    color_band,
    combine_scores,
    decompose_nominal,
    expanding_percentile,
    fiscal_confidence_stress,
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


def month_index(n=60, start="2000-01-01"):
    return pd.date_range(start, periods=n, freq="MS")


class TestCatalog(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertAlmostEqual(WEIGHT_SUM, 1.0)
        self.assertGreaterEqual(len(KPI_SPECS), 8)
        layers = {s.layer for s in KPI_SPECS}
        self.assertEqual(layers, {"rate", "term_premium", "fiscal", "transmission"})

    def test_acm_is_primary_term_premium(self):
        primary = next(s for s in KPI_SPECS if s.key == "acm_tp10")
        self.assertIn("ACMTP10", primary.series_id)
        self.assertTrue(primary.primary_model)
        self.assertIn("newyorkfed.org", primary.source_url)

    def test_does_not_treat_news_as_source(self):
        for spec in KPI_SPECS:
            self.assertNotIn("bloomberg", spec.source.lower())
            self.assertNotIn("reuters", spec.source.lower())


class TestPercentile(unittest.TestCase):
    def test_needs_min_history(self):
        self.assertIsNone(expanding_percentile(np.arange(10), 5, False))

    def test_high_value_high_rank(self):
        hist = np.arange(1, 31)
        self.assertGreater(expanding_percentile(hist, 30, False), 95)
        self.assertLess(expanding_percentile(hist, 1, False), 10)


class TestScoreSeries(unittest.TestCase):
    def test_rising_yield_ends_high(self):
        spec = next(s for s in KPI_SPECS if s.key == "dgs10")
        s = pd.Series(np.linspace(1.5, 5.0, 72), index=month_index(72))
        df = score_series(spec, s)
        self.assertGreater(df["score"].iloc[-1], 70)
        self.assertAlmostEqual(df["change"].iloc[-1], s.iloc[-1] - s.iloc[-2])
        self.assertIn("chg_1m", df.columns)
        self.assertIn("chg_3m", df.columns)

    def test_does_not_invent_values(self):
        spec = next(s for s in KPI_SPECS if s.key == "dgs10")
        s = pd.Series([1.0, np.nan, 2.0], index=month_index(3))
        df = score_series(spec, s)
        self.assertEqual(len(df), 2)


class TestDecomposition(unittest.TestCase):
    def test_nominal_minus_real_minus_bei_is_residual(self):
        idx = month_index(4)
        nom = pd.Series([4.0, 4.2, 4.5, 4.9], index=idx)
        real = pd.Series([1.5, 1.6, 1.8, 2.0], index=idx)
        bei = pd.Series([2.4, 2.5, 2.6, 2.7], index=idx)
        out = decompose_nominal(nom, real, bei)
        self.assertAlmostEqual(out["residual"].iloc[-1], 4.9 - 2.0 - 2.7)
        self.assertAlmostEqual(out["real_share"].iloc[-1] + out["bei_share"].iloc[-1] + out["residual_share"].iloc[-1], 1.0, places=6)

    def test_acm_identity(self):
        idx = month_index(3)
        nom = pd.Series([4.0, 4.5, 5.0], index=idx)
        tp = pd.Series([0.4, 0.7, 1.2], index=idx)
        out = decompose_nominal(nom, None, None, expected_short=nom - tp, term_premium=tp)
        self.assertAlmostEqual(out["expected_short"].iloc[-1], 3.8)
        self.assertAlmostEqual(out["term_premium"].iloc[-1], 1.2)


class TestAcceleration(unittest.TestCase):
    def test_stable_high_tp_is_not_acceleration(self):
        s = pd.Series(np.full(36, 1.2), index=month_index(36))
        state = tp_acceleration_state(s)
        self.assertEqual(state["state"], "STABLE")
        self.assertFalse(state["acceleration"])

    def test_step_up_is_acceleration(self):
        vals = np.concatenate([np.full(32, 0.4), [0.7, 1.0, 1.2, 1.2]])
        s = pd.Series(vals, index=month_index(36))
        state = tp_acceleration_state(s)
        self.assertEqual(state["state"], "ACCELERATING")
        self.assertTrue(state["acceleration"])
        self.assertGreater(state["chg_3m"], 0.3)


class TestScores(unittest.TestCase):
    def _scored(self, high=False):
        scored = {}
        for spec in KPI_SPECS:
            n = 72
            if high:
                if spec.higher_is_stress:
                    vals = np.linspace(0.2, 8.0, n)
                else:
                    vals = np.linspace(8.0, 0.2, n)
            else:
                vals = np.full(n, 2.0)
            s = pd.Series(vals, index=month_index(n))
            scored[spec.key] = score_series(spec, s)
        return scored

    def test_missing_panel_does_not_invent_overall(self):
        scored = self._scored()
        for key in list(scored)[:6]:
            del scored[key]
        hist = combine_scores(scored)
        self.assertTrue(hist["trrs"].isna().all())

    def test_full_panel_computes(self):
        hist = combine_scores(self._scored(high=True))
        self.assertFalse(hist["trrs"].isna().iloc[-1])
        self.assertGreater(hist["trrs"].iloc[-1], 50)
        self.assertEqual(hist["confidence"].iloc[-1], "HIGH")

    def test_sub_scores_exist(self):
        hist = combine_scores(self._scored(high=True))
        row = hist.iloc[-1]
        self.assertGreater(row["rate_stress"], 40)
        self.assertGreater(row["tp_stress"], 40)
        self.assertGreater(row["fiscal_stress"], 40)


class TestRegime(unittest.TestCase):
    def test_2013_like_growth_driven(self):
        out = classify_regime(
            real_chg_3m=0.8,
            bei_chg_3m=0.05,
            tp_chg_3m=0.15,
            tp_accelerating=False,
            deficit_stress=40,
        )
        self.assertEqual(out["regime"], "2013-LIKE GROWTH SHOCK")

    def test_2023_like_term_premium(self):
        out = classify_regime(
            real_chg_3m=0.2,
            bei_chg_3m=0.1,
            tp_chg_3m=0.7,
            tp_accelerating=True,
            deficit_stress=45,
        )
        self.assertEqual(out["regime"], "2023-LIKE TERM PREMIUM SHOCK")

    def test_fiscal_confidence(self):
        out = classify_regime(
            real_chg_3m=0.4,
            bei_chg_3m=0.1,
            tp_chg_3m=0.6,
            tp_accelerating=True,
            deficit_stress=80,
        )
        self.assertEqual(out["regime"], "FISCAL-CONFIDENCE SHOCK")


class TestLabels(unittest.TestCase):
    def test_status_and_color(self):
        self.assertEqual(status_label(18), "NORMAL")
        self.assertEqual(status_label(35), "ELEVATED")
        self.assertEqual(status_label(52), "WARNING")
        self.assertEqual(status_label(68), "STRESS")
        self.assertEqual(status_label(82), "REGIME BREAK RISK")
        self.assertEqual(status_label(93), "REGIME BREAK RISK")
        self.assertEqual(color_band(20), "green")
        self.assertEqual(color_band(50), "yellow")
        self.assertEqual(color_band(70), "orange")
        self.assertEqual(color_band(85), "red")
        self.assertEqual(status_label(None), "UNAVAILABLE")

    def test_helpers_are_wrappers(self):
        self.assertEqual(rate_stress.__name__, "rate_stress")
        self.assertEqual(term_premium_stress.__name__, "term_premium_stress")
        self.assertEqual(fiscal_confidence_stress.__name__, "fiscal_confidence_stress")
        self.assertEqual(treasury_regime_risk.__name__, "treasury_regime_risk")


class TestConfigAndUnits(unittest.TestCase):
    def test_final_weights(self):
        self.assertEqual(CONFIG["final_weights"], {"rate": 0.30, "term_premium": 0.40, "fiscal": 0.30})
        self.assertAlmostEqual(sum(CONFIG["final_weights"].values()), 1.0)

    def test_one_percent_is_100bp(self):
        self.assertEqual(basis_points(1.0), 100.0)
        self.assertEqual(basis_points(0.50), 50.0)

    def test_risk_bands_are_ordered(self):
        cuts = [row["max"] for row in CONFIG["risk_bands"]]
        self.assertEqual(cuts, sorted(cuts))
        self.assertEqual(CONFIG["risk_bands"][-1]["label"], "REGIME BREAK RISK")

    def test_component_score_reports_partial_coverage(self):
        out = weighted_component_score({"a": 80.0, "b": None}, {"a": 0.7, "b": 0.3})
        self.assertEqual(out["score"], 80.0)
        self.assertAlmostEqual(out["coverage"], 0.7)
        self.assertEqual(out["quality"], "PARTIAL")
        self.assertEqual(out["missing"], ["b"])

    def test_component_score_blocks_low_coverage(self):
        out = weighted_component_score({"a": 80.0, "b": None}, {"a": 0.4, "b": 0.6})
        self.assertIsNone(out["score"])
        self.assertEqual(out["quality"], "MISSING")


class TestRollingStatistics(unittest.TestCase):
    def test_zscore_uses_past_window(self):
        s = pd.Series(np.arange(30, dtype=float), index=month_index(30))
        z = rolling_zscore(s, window=20, min_periods=10)
        self.assertGreater(z.iloc[-1], 1.0)
        self.assertTrue(pd.isna(z.iloc[0]))

    def test_tp_slopes_detect_acceleration(self):
        idx = pd.date_range("2025-01-01", periods=100, freq="B")
        values = np.concatenate([np.linspace(0.2, 0.5, 60), np.linspace(0.5, 1.4, 40)])
        out = term_premium_slopes(pd.Series(values, index=idx))
        self.assertGreater(out["slope_20_bp_per_day"], out["slope_60_bp_per_day"])
        self.assertTrue(out["accelerating"])


class TestAccelerationOverride(unittest.TestCase):
    def test_30bp_sets_minimum_elevated(self):
        out = apply_acceleration_override(10.0, 31.0, 75.0, CONFIG)
        self.assertGreaterEqual(out["score"], 25.0)
        self.assertEqual(out["minimum_band"], "ELEVATED")

    def test_50bp_sets_minimum_warning(self):
        out = apply_acceleration_override(15.0, 55.0, 90.0, CONFIG)
        self.assertGreaterEqual(out["score"], 45.0)
        self.assertEqual(out["minimum_band"], "WARNING")

    def test_no_override_below_calibrated_percentile(self):
        out = apply_acceleration_override(15.0, 55.0, 50.0, CONFIG)
        self.assertFalse(out["applied"])
        self.assertEqual(out["score"], 15.0)


class TestBacktest(unittest.TestCase):
    def test_forward_returns_and_drawdown(self):
        idx = pd.date_range("2000-01-03", periods=800, freq="B")
        tp = pd.Series(np.sin(np.arange(800) / 20) * 0.2, index=idx)
        tp.iloc[300:365] += np.linspace(0, 1.0, 65)
        px = pd.Series(np.linspace(100, 180, 800), index=idx)
        px.iloc[365:390] *= np.linspace(1.0, 0.8, 25)
        out = backtest_events(tp, px, px * 1.2, start="2000-01-01")
        self.assertGreaterEqual(out["definitions"]["union"]["sample_size"], 1)
        row = out["definitions"]["union"]["horizons"]["3M"]
        self.assertIn("average", row)
        self.assertIn("median", row)
        self.assertIn("win_rate", row)
        self.assertIn("max_drawdown", row)
        self.assertIn("p25", row)
        self.assertIn("p75", row)
        self.assertGreaterEqual(row["sample_size"], 1)


if __name__ == "__main__":
    unittest.main()
