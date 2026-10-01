import unittest
from pathlib import Path
from unittest.mock import Mock

from fetch_all_history import rank_main_targets, resolve_fresh_capture_pair


class RouteSelectionTests(unittest.TestCase):
    def test_prefers_newer_candidate_index_without_assuming_hour_or_port_suffix(self):
        routes = {
            "operate-global-game-8.operate-global-game.z1-p99999",
            "operate-global-game-11.operate-global-game.z1-p11222",
        }
        self.assertEqual(rank_main_targets(routes)[0],
                         "operate-global-game-11.operate-global-game.z1-p11222")

    def test_fresh_capture_pair_contains_frame_triples_not_nested_route(self):
        gateway = (b"gateway-auth", b"gateway-login", b"same-credential")
        main = (b"main-auth", b"main-login", b"same-credential")
        passive = Mock()
        passive.main_login_frames.return_value = ("operate-global-game-11.operate-global-game.z1-p11222", main)
        result = resolve_fresh_capture_pair(passive, (gateway, main), Path("fresh.pcap"), 12887)
        self.assertEqual(result, (gateway, "operate-global-game-11.operate-global-game.z1-p11222", main))
        passive.main_login_frames.assert_called_once_with(Path("fresh.pcap"), 12887, gateway[2])


if __name__ == "__main__":
    unittest.main()
