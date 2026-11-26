import unittest
from engine import choose_route, calculate_costs, replenishment_qty

class EngineTests(unittest.TestCase):
    def test_mesh_selected_when_cheaper_and_feasible(self):
        result=choose_route(True,True,100,400,2400,130,1500,62)
        self.assertEqual(result["route"],"Mesh")
        self.assertAlmostEqual(result["mesh_cost_per_order"],24)

    def test_hub_selected_when_stock_missing(self):
        result=choose_route(False,True,100,400,2400,130,1500,62)
        self.assertEqual(result["route"],"Hub")

    def test_hub_selected_when_capacity_exceeded(self):
        result=choose_route(True,True,131,524,2400,130,1500,62)
        self.assertEqual(result["route"],"Hub")

    def test_hub_selected_when_mesh_not_economical(self):
        result=choose_route(True,True,20,80,2400,130,1500,62)
        self.assertEqual(result["route"],"Hub")

    def test_replenishment_respects_incoming_and_reserved(self):
        self.assertEqual(replenishment_qty(10,5,3,4,3,2),2)
        self.assertEqual(replenishment_qty(2,0,1,4,3,2),13)

    def test_finance_totals(self):
        result=calculate_costs(100,60,40,62,2400,2,14.5,1000)
        self.assertEqual(result["hub_baseline"],6200)
        self.assertEqual(result["combined_total"],2*2400+60*14.5+40*62+1000)

if __name__=="__main__":
    unittest.main()
