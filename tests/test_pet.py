"""獬豸桌宠成长进度 API 的回归测试。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from legalweb.app import create_app
from legalweb.database import Database, now_iso


def make_client():
    directory = tempfile.mkdtemp()
    with patch.dict(os.environ, {"LAW_DATA_DIR": directory}), \
            patch("legalweb.app.seed_sources"):
        app = create_app()
    return app.test_client(), Database(Path(directory))


class PetProgressTests(unittest.TestCase):
    def test_new_client_gets_zero_cub_state(self):
        client, _ = make_client()
        state = client.get("/api/pet?client_id=browser-0001").get_json()
        self.assertEqual(state["exp"], 0)
        self.assertEqual(state["stage"], "cub")
        self.assertEqual(state["stage_name"], "幼獬")
        self.assertEqual(state["next_stage_name"], "启蒙")
        self.assertEqual(state["next_need"], 30)
        self.assertEqual(state["qa_count"], 0)
        self.assertEqual(len(state["stages"]), 6)

    def test_stage_milestones_and_progress(self):
        client, database = make_client()
        with database.connect() as db:
            db.execute("INSERT INTO pet_progress (client_id,exp,updated_at)"
                       " VALUES ('browser-0001',35,?)", (now_iso(),))
        state = client.get("/api/pet?client_id=browser-0001").get_json()
        # 35 经验：过启蒙(30)，未到明辨(100)
        self.assertEqual(state["stage"], "dawn")
        self.assertEqual(state["next_stage_name"], "明辨")
        self.assertEqual(state["next_need"], 65)
        # (35-30)/(100-30) ≈ 7%
        self.assertEqual(state["progress"], 7)

    def test_tick_upsert_accumulates_without_row_loss(self):
        # 与 app.py pet_tick 相同的 upsert 语句：验证累加语义与计数列
        _, database = make_client()
        sql = ("INSERT INTO pet_progress (client_id,exp,qa_count,court_count,updated_at)"
               " VALUES (?,?,?,?,?)"
               " ON CONFLICT(client_id) DO UPDATE SET"
               " exp=pet_progress.exp+excluded.exp,"
               " qa_count=pet_progress.qa_count+excluded.qa_count,"
               " court_count=pet_progress.court_count+excluded.court_count,"
               " updated_at=excluded.updated_at")
        with database.connect() as db:
            db.execute(sql, ("browser-0001", 2, 1, 0, now_iso()))
            db.execute(sql, ("browser-0001", 2, 1, 0, now_iso()))
            db.execute(sql, ("browser-0001", 10, 0, 1, now_iso()))
            row = db.execute("SELECT exp,qa_count,court_count FROM pet_progress"
                             " WHERE client_id='browser-0001'").fetchone()
        self.assertEqual(tuple(row), (14, 2, 1))

    def test_invalid_client_id_rejected(self):
        client, _ = make_client()
        self.assertEqual(client.get("/api/pet?client_id=abc").status_code, 400)


if __name__ == "__main__":
    unittest.main()
