import unittest

from financial_model import forecast_financials, break_even_anchor_fee
from engine import evaluate_batch


class FinancialModelTests(unittest.TestCase):
    def test_full_mesh_cost_includes_trip_variable_anchor_replenishment_storage_and_system(self):
        result = forecast_financials(
            monthly_orders=1200, mesh_share_pct=100, anchors=3, batch_orders=80,
            trip_cost=2400, hub_cost_per_order=62, variable_cost=14.5,
            anchor_fee=3.5, replenishment_per_order=9, storage_per_order=2.5,
            monthly_system_cost=10000, months=1,
        )["monthly"]
        self.assertEqual(result["trips"], 15)
        self.assertAlmostEqual(result["mesh_cost"], 81400.0)
        self.assertAlmostEqual(result["baseline_hub_cost"], 74400.0)
        self.assertAlmostEqual(result["savings"], -7000.0)
        self.assertAlmostEqual(result["mesh_cost_per_order"], 81400 / 1200, places=2)

    def test_130_capacity_reduces_trips_and_changes_scenario_to_small_saving(self):
        result = forecast_financials(
            monthly_orders=1200, mesh_share_pct=100, anchors=3, batch_orders=130,
            trip_cost=2400, hub_cost_per_order=62, variable_cost=14.5,
            anchor_fee=3.5, replenishment_per_order=9, storage_per_order=2.5,
            monthly_system_cost=10000, months=1,
        )["monthly"]
        self.assertEqual(result["trips"], 12)
        self.assertAlmostEqual(result["mesh_cost"], 74200.0)
        self.assertAlmostEqual(result["savings"], 200.0)

    def test_fixed_system_cost_is_not_underallocated_for_partial_mesh_share(self):
        result = forecast_financials(
            monthly_orders=1200, mesh_share_pct=50, anchors=3, batch_orders=80,
            trip_cost=2400, hub_cost_per_order=62, variable_cost=14.5,
            anchor_fee=3.5, replenishment_per_order=9, storage_per_order=2.5,
            monthly_system_cost=10000, months=1,
        )["monthly"]
        self.assertEqual(result["mesh_orders"], 600)
        self.assertEqual(result["hub_orders"], 600)
        self.assertAlmostEqual(result["system_cost"], 10000.0)
        self.assertAlmostEqual(result["savings"], -12100.0)

    def test_break_even_anchor_fee_is_about_3_67_at_130_capacity(self):
        fee = break_even_anchor_fee(
            orders_per_month=1200, anchors=3, batch_orders=130,
            trip_cost=2400, hub_cost_per_order=62, variable_cost=14.5,
            replenishment_per_order=9, storage_per_order=2.5,
            monthly_system_cost=10000,
        )
        self.assertAlmostEqual(fee, 3.6666666667, places=6)

    def test_operational_batch_routing_accounts_for_additional_costs(self):
        result = evaluate_batch(
            80, 320, 5, 2400, 14.5, 62, 80, 130, 1500, 5,
            additional_cost_per_order=23.3333333333,
        )
        self.assertEqual(result["decision"], "hub")
        self.assertGreater(result["mesh_cost_per_order"], 62)


if __name__ == "__main__":
    unittest.main()
