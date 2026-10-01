import unittest

from fetch_all_history import rank_main_targets


class RouteSelectionTests(unittest.TestCase):
    def test_prefers_newer_candidate_index_without_assuming_hour_or_port_suffix(self):
        routes = {
            "operate-global-game-8.operate-global-game.z1-p99999",
            "operate-global-game-11.operate-global-game.z1-p11222",
        }
        self.assertEqual(rank_main_targets(routes)[0],
                         "operate-global-game-11.operate-global-game.z1-p11222")


if __name__ == "__main__":
    unittest.main()
