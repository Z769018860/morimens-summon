import sqlite3
import tempfile
import unittest
from pathlib import Path

import history_store as store


def record(tid, timestamp, history_type=2):
    return {"itemTid": tid, "name": f"Item_{tid}_Name|物品{tid}",
            "timestamp": timestamp, "type": history_type, "playerId": 123}


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = store.connect(Path(self.tmp.name) / "history.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_resume_and_increment_with_same_second_records(self):
        initial = [record(100 + i, 500 - i // 3) for i in range(12)]
        store.ingest_page(self.db, 2, 1, {"count": 12, "records": initial[:5]})
        self.assertEqual(store.missing_pages(self.db, 2, 12), [2, 3])
        store.ingest_page(self.db, 2, 2, {"count": 12, "records": initial[5:10]})
        store.ingest_page(self.db, 2, 3, {"count": 12, "records": initial[10:]})
        self.assertEqual(store.missing_pages(self.db, 2, 12), [])
        updated = [record(200 + i, 501, 2) for i in range(3)] + initial
        store.ingest_page(self.db, 2, 1, {"count": 15, "records": updated[:5]})
        self.assertEqual(store.missing_pages(self.db, 2, 15), [])
        self.assertEqual(store.coverage(self.db)[0]["known"], 15)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM history_records").fetchone()[0], 15)

    def test_detects_mutation_without_overwrite(self):
        page = [record(100 + i, 500) for i in range(5)]
        store.ingest_page(self.db, 2, 1, {"count": 5, "records": page})
        altered = [record(999, 500)] + page[1:]
        with self.assertRaises(ValueError):
            store.ingest_page(self.db, 2, 1, {"count": 5, "records": altered})
        self.assertEqual(self.db.execute("SELECT item_tid FROM history_records WHERE ordinal=4").fetchone()[0], 100)

    def test_uid_is_saved_and_mixed_accounts_are_rejected(self):
        store.ingest_page(self.db, 2, 1, {"count": 1, "records": [record(100, 500)]})
        self.assertEqual(store.account_uid(self.db), "123")
        other = {**record(101, 501), "playerId": 456}
        with self.assertRaisesRegex(ValueError, "账号不一致"):
            store.ingest_page(self.db, 2, 1, {"count": 1, "records": [other]})
        self.assertEqual(store.account_uid(self.db), "123")

    def test_empty_category(self):
        store.ingest_page(self.db, 17, 1, {"count": 0})
        self.assertEqual(store.coverage(self.db)[0]["complete"], True)


if __name__ == "__main__":
    unittest.main()
