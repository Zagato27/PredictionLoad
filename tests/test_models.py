import json
import os
import unittest

import numpy as np

from services.models import mm1_response_time_ms, erlang_c_wait_probability, mmc_response_time_ms
from services.runner import dump_forecast, run_forecast
from services.schemas import InputSchema
from services.forecast import (
    _predict_empirical_pair,
    _predict_observed_latency,
    compute_forecast,
)


def _load_sample() -> dict:
    root = os.path.dirname(os.path.dirname(__file__))
    sample = os.path.join(root, "data", "sample.json")
    with open(sample, "r", encoding="utf-8") as f:
        return json.load(f)


def _validate(payload: dict) -> InputSchema:
    try:
        return InputSchema.model_validate(payload)
    except AttributeError:
        return InputSchema.parse_obj(payload)  # type: ignore


class TestModels(unittest.TestCase):
    def test_mm1_response(self):
        self.assertAlmostEqual(mm1_response_time_ms(0.5, 1000), 1.0, places=3)

    def test_erlang_c_basic(self):
        Pw = erlang_c_wait_probability(a=0.5, c=4)
        self.assertTrue(0.0 <= Pw <= 1.0)

    def test_mmc_response(self):
        R, Pw = mmc_response_time_ms(20.0, 100.0, 4)
        self.assertTrue(R > 0.0)
        self.assertTrue(0.0 <= Pw <= 1.0)

    def test_forecast_on_sample(self):
        out = compute_forecast(_validate(_load_sample()))
        res = out.model_dump() if hasattr(out, "model_dump") else out.dict()
        self.assertIn("targets", res)
        self.assertIn("models", res)
        self.assertIn("series", res)
        self.assertIsNotNone(res["targets"]["latency_ms"]["empirical"])
        self.assertGreaterEqual(
            res["targets"]["latency_ms"]["empirical"]["max"],
            res["targets"]["latency_ms"]["empirical"]["avg"],
        )
        self.assertLess(res["targets"]["instances"]["cpu_based"], 10)
        self.assertIn("io_based", res["targets"]["instances"])
        self.assertIn("ram", res["targets"]["utilization"])
        self.assertIn("ram_based", res["targets"]["instances"])
        self.assertIn("cpu_demand", res["series"]["util_vs_rps"][0])
        self.assertIn("instances_ram", res["series"]["instances_vs_rps"][0])
        self.assertNotIn("network", res)
        self.assertNotIn("net_vs_rps", res["series"])
        self.assertEqual(res["models"]["service_time_ms"], 28.0)

    def test_observed_latency_flat_when_constant(self):
        rps = np.array([10.0, 20.0, 30.0, 40.0], dtype=float)
        latency = np.array([50.0, 50.0, 50.0, 50.0], dtype=float)
        for target in (15.0, 40.0, 100.0):
            self.assertAlmostEqual(_predict_observed_latency(rps, latency, target), 50.0, places=6)

    def test_observed_latency_follows_dip_after_peak(self):
        rps = np.array([21.0, 50.0, 62.0, 90.0], dtype=float)
        latency = np.array([28.0, 29.7, 28.0, 28.0], dtype=float)
        self.assertAlmostEqual(_predict_observed_latency(rps, latency, 62.0), 28.0, places=6)
        self.assertAlmostEqual(_predict_observed_latency(rps, latency, 90.0), 28.0, places=6)

    def test_observed_latency_extrapolates_only_on_positive_trend(self):
        rps = np.array([10.0, 20.0, 30.0], dtype=float)
        rising = np.array([40.0, 50.0, 60.0], dtype=float)
        flat = np.array([40.0, 50.0, 50.0], dtype=float)
        self.assertAlmostEqual(_predict_observed_latency(rps, rising, 40.0), 70.0, places=6)
        self.assertAlmostEqual(_predict_observed_latency(rps, flat, 40.0), 50.0, places=6)

    def test_empirical_pair_max_ge_avg_on_extrapolation(self):
        rps = np.array([10.0, 20.0, 30.0], dtype=float)
        avg = np.array([100.0, 120.0, 130.0], dtype=float)
        max_values = np.array([200.0, 210.0, 205.0], dtype=float)
        avg_ms, max_ms = _predict_empirical_pair(rps, avg, max_values, 50.0)
        self.assertGreaterEqual(max_ms, avg_ms)

    def test_util_semantics_on_sample(self):
        out = compute_forecast(_validate(_load_sample()))
        point = next(p for p in out.series.util_vs_rps if p.rps == 21.0)
        self.assertIsNotNone(point.cpu_demand)
        self.assertIsNotNone(point.cpu)
        self.assertLessEqual(point.cpu, point.cpu_demand)

    def test_per_pod_util_below_demand_with_multiple_pods(self):
        out = compute_forecast(_validate(_load_sample()))
        for point in out.series.util_vs_rps:
            if point.cpu_demand and point.cpu:
                self.assertLessEqual(point.cpu, point.cpu_demand + 1e-9)

    def test_suggested_m_includes_io(self):
        payload = _load_sample()
        for step in payload["steps"]:
            step["io_util"] = 0.95
        payload["capacity"]["u_max_io_optional"] = 0.5
        out = compute_forecast(_validate(payload))
        self.assertGreater(out.targets.instances.io_based, 1)
        self.assertGreaterEqual(out.targets.instances.suggested_m, out.targets.instances.io_based)

    def test_queueing_mmc_finite_on_sample_target(self):
        out = compute_forecast(_validate(_load_sample()))
        mmc = out.targets.latency_ms.m_m_c
        if mmc is not None:
            self.assertTrue(mmc.avg is None or mmc.avg < 1e6)
        else:
            self.assertTrue(out.meta.has_unstable_models)

    def test_dump_forecast_has_no_infinity(self):
        dumped = dump_forecast(compute_forecast(_validate(_load_sample())))
        encoded = json.dumps(dumped)
        self.assertNotIn("Infinity", encoded)
        self.assertNotIn("-Infinity", encoded)

    def test_duplicate_rps_averaged(self):
        payload = _load_sample()
        payload["steps"] = payload["steps"][:2]
        payload["steps"][1]["rps"] = payload["steps"][0]["rps"]
        payload["steps"][1]["avg_ms"] = 40.0
        payload["steps"][1]["max_ms"] = 80.0
        out = compute_forecast(_validate(payload))
        point = next(p for p in out.series.latency_vs_rps if p.rps == payload["steps"][0]["rps"])
        self.assertAlmostEqual(point.observed_avg_ms, 34.0, places=6)

    def test_high_error_step_excluded_from_fit(self):
        payload = _load_sample()
        payload["steps"][0]["errors_pct"] = 50.0
        payload["steps"][0]["cpu_usage_m"] = 9999
        out = compute_forecast(_validate(payload))
        self.assertEqual(out.meta.excluded_steps, 1)
        self.assertLess(out.targets.utilization.cpu, 50.0)


if __name__ == "__main__":
    unittest.main()
