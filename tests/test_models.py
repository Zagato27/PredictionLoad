import json
import os
import unittest

from services.models import mm1_response_time_ms, erlang_c_wait_probability, mmc_response_time_ms
from services.schemas import InputSchema
from services.forecast import compute_forecast


class TestModels(unittest.TestCase):
    def test_mm1_response(self):
        # S = 0.5 ms, X = 1000 rps -> rho = 0.5 -> R = S/(1-rho) = 1.0 ms
        self.assertAlmostEqual(mm1_response_time_ms(0.5, 1000), 1.0, places=3)

    def test_erlang_c_basic(self):
        # light traffic: a << c -> low wait probability
        Pw = erlang_c_wait_probability(a=0.5, c=4)
        self.assertTrue(0.0 <= Pw <= 1.0)

    def test_mmc_response(self):
        R, Pw = mmc_response_time_ms(20.0, 100.0, 4)
        self.assertTrue(R > 0.0)
        self.assertTrue(0.0 <= Pw <= 1.0)

    def test_forecast_on_sample(self):
        root = os.path.dirname(os.path.dirname(__file__))
        sample = os.path.join(root, "data", "sample.json")
        with open(sample, "r", encoding="utf-8") as f:
            payload = json.load(f)
        try:
            data = InputSchema.model_validate(payload)  # v2
        except AttributeError:
            data = InputSchema.parse_obj(payload)  # type: ignore
        out = compute_forecast(data)
        try:
            res = out.model_dump()
        except AttributeError:
            res = out.dict()
        self.assertIn("targets", res)
        self.assertIn("models", res)
        self.assertIn("series", res)
        # Sanity checks near brief example
        self.assertGreater(res["targets"]["latency_ms"]["m_m_1"]["avg"], 10.0)
        self.assertLess(res["targets"]["instances"]["cpu_based"], 10)
        # RAM presence checks
        self.assertIn("ram", res["targets"]["utilization"])
        self.assertIn("ram_based", res["targets"]["instances"])
        # Series include RAM
        self.assertIn("ram", res["series"]["util_vs_rps"][0])
        self.assertIn("instances_ram", res["series"]["instances_vs_rps"][0])
        # Network predictions present
        self.assertIn("network", res)
        self.assertIn("net_vs_rps", res["series"])


if __name__ == "__main__":
    unittest.main()


