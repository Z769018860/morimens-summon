import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import history_store


class FakeProcess:
    def __init__(self, lines, code):
        self.stdout = io.StringIO(lines)
        self.code = code

    def wait(self):
        return self.code


class UpdateTests(unittest.TestCase):
    def test_single_query_process_reports_saved_partial_progress(self):
        fake = FakeProcess("CURRENT_AUTH_IN_RAM pid 1 target game\n"
                           "HISTORY_PAGE 10 25 5 of 505\n"
                           "CONNECTION_PAUSED RuntimeError after 1 pages\n", 0)
        with patch.object(app.subprocess, "Popen", return_value=fake) as popen:
            app.UPDATE.update(running=True, phase="prepare", message="", log=[], result=None)
            app.update_worker()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(app.UPDATE["result"], "partial")
        self.assertFalse(app.UPDATE["running"])
        self.assertTrue(any("类别 10 第 25 页" in line for line in app.UPDATE["log"]))

    def test_api_payload_excludes_login_and_player_id(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.sqlite3'
            db = history_store.connect(path)
            try:
                history_store.ingest_page(db, 2, 1, {'count': 1, 'records': [
                    {'type': 2, 'itemTid': 123, 'name': 'Item_123_Name|测试',
                     'timestamp': 1780000000, 'playerId': 987654321}]})
            finally:
                db.close()
            data = app.payload(db_path=path)
        self.assertTrue(data["records"])
        self.assertEqual(data["uid"], "987654321")
        self.assertEqual(set(data["records"][0]),
                         {"history_type", "ordinal", "item_tid", "name", "timestamp"})

    def test_running_game_without_auth_reports_relogin_action(self):
        fake = FakeProcess("NO_CURRENT_AUTH\n", 2)
        game = type("Game", (), {"info": {"name": "Morimens.exe"}})()
        with patch.object(app.subprocess, "Popen", return_value=fake), \
             patch.object(app.psutil, "process_iter", return_value=[game]):
            app.UPDATE.update(running=True, phase="prepare", message="", log=[], result=None)
            app.update_worker()
        self.assertEqual(app.UPDATE["result"], "failed")
        self.assertEqual(app.UPDATE["reason"], "auth_unavailable")
        self.assertIn("重新登录", app.UPDATE["message"])


if __name__ == "__main__":
    unittest.main()
