import unittest
from engine import choose_route, calculate_costs, replenishment_qty, evaluate_batch, plan_multi_stop_routes


class EngineTests(unittest.TestCase):
    def test_mesh_selected_when_cheaper_and_feasible(self):
        result = choose_route(True, True, 100, 400, 2400, 130, 1500, 62)
        self.assertEqual(result["route"], "Mesh")
        self.assertAlmostEqual(result["mesh_cost_per_order"], 24)

    def test_hub_selected_when_stock_missing(self):
        result = choose_route(False, True, 100, 400, 2400, 130, 1500, 62)
        self.assertEqual(result["route"], "Hub")

    def test_hub_selected_when_capacity_exceeded(self):
        result = choose_route(True, True, 131, 524, 2400, 130, 1500, 62)
        self.assertEqual(result["route"], "Hub")

    def test_hub_selected_when_mesh_not_economical(self):
        result = choose_route(True, True, 20, 80, 2400, 130, 1500, 62)
        self.assertEqual(result["route"], "Hub")

    def test_replenishment_respects_incoming_and_reserved(self):
        self.assertEqual(replenishment_qty(10, 5, 3, 4, 3, 2), 2)
        self.assertEqual(replenishment_qty(2, 0, 1, 4, 3, 2), 13)

    def test_finance_totals(self):
        result = calculate_costs(100, 60, 40, 62, 2400, 2, 14.5, 1000)
        self.assertEqual(result["hub_baseline"], 6200)
        self.assertEqual(result["combined_total"], 2 * 2400 + 60 * 14.5 + 40 * 62 + 1000)

    def test_real_batch_waits_before_target_and_timeout(self):
        result = evaluate_batch(50, 200, 2, 2400, 14.5, 62, 80, 130, 1500, 5)
        self.assertEqual(result["decision"], "wait")

    def test_real_batch_mesh_cost_uses_actual_order_count(self):
        result = evaluate_batch(80, 320, 1, 2400, 14.5, 62, 80, 130, 1500, 5)
        self.assertEqual(result["decision"], "mesh")
        self.assertAlmostEqual(result["mesh_cost_per_order"], 44.5)

    def test_timeout_falls_back_to_hub_when_mesh_is_more_expensive(self):
        result = evaluate_batch(50, 200, 5, 2400, 14.5, 62, 80, 130, 1500, 5)
        self.assertEqual(result["decision"], "hub")
        self.assertGreater(result["mesh_cost_per_order"], 62)

    def test_timeout_can_dispatch_mesh_when_actual_batch_is_economical(self):
        result = evaluate_batch(60, 240, 5, 2400, 14.5, 62, 80, 130, 1500, 5)
        self.assertEqual(result["decision"], "mesh")
        self.assertAlmostEqual(result["mesh_cost_per_order"], 54.5)

    def test_volume_capacity_triggers_batch_decision(self):
        result = evaluate_batch(60, 1500, 1, 2400, 14.5, 62, 80, 130, 1500, 5)
        self.assertEqual(result["decision"], "mesh")

    def test_over_capacity_batch_is_rejected(self):
        result = evaluate_batch(131, 100, 1, 2400, 14.5, 62, 80, 130, 1500, 5)
        self.assertEqual(result["decision"], "capacity_error")

    def test_multi_stop_planner_groups_nearby_pvz(self):
        anchor = {"lat": 56.631364, "lon": 47.890917}
        stops = [
            {"point_id": 1, "name": "PVZ A", "lat": 56.6320, "lon": 47.8920, "orders": 20, "volume_l": 80},
            {"point_id": 2, "name": "PVZ B", "lat": 56.6330, "lon": 47.8930, "orders": 25, "volume_l": 90},
        ]
        result = plan_multi_stop_routes(anchor, stops, 130, 1500, 2400, 14.5, 62, 2.0)
        self.assertEqual(len(result["routes"]), 1)
        self.assertEqual(result["routes"][0]["total_orders"], 45)
        self.assertEqual(len(result["routes"][0]["stops"]), 2)

    def test_multi_stop_planner_separates_remote_pvz(self):
        anchor = {"lat": 56.631364, "lon": 47.890917}
        stops = [
            {"point_id": 1, "name": "PVZ Near", "lat": 56.6320, "lon": 47.8920, "orders": 20, "volume_l": 80},
            {"point_id": 2, "name": "PVZ Remote", "lat": 56.6500, "lon": 47.9500, "orders": 25, "volume_l": 90},
        ]
        result = plan_multi_stop_routes(anchor, stops, 130, 1500, 2400, 14.5, 62, 2.0)
        self.assertEqual(len(result["routes"]), 2)
        self.assertEqual([len(r["stops"]) for r in result["routes"]], [1, 1])

    def test_multi_stop_planner_respects_capacity(self):
        anchor = {"lat": 56.631364, "lon": 47.890917}
        stops = [
            {"point_id": 1, "name": "PVZ A", "lat": 56.6320, "lon": 47.8920, "orders": 80, "volume_l": 400},
            {"point_id": 2, "name": "PVZ B", "lat": 56.6330, "lon": 47.8930, "orders": 60, "volume_l": 300},
        ]
        result = plan_multi_stop_routes(anchor, stops, 100, 1500, 2400, 14.5, 62, 2.0)
        self.assertEqual(len(result["routes"]), 2)
        self.assertTrue(all(r["total_orders"] <= 100 for r in result["routes"]))


if __name__ == "__main__":
    unittest.main()
