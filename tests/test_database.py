"""Tests for the data module. Run from the KeystrokeID directory:
    python -m unittest discover -s tests -t . -v      (or: python -m pytest)
"""
import csv
import os
from contextlib import closing
import sqlite3
import tempfile
import unittest

from data import database as db
from data.features import extract_features, feature_names
from data.schemas import PROMPTS, SampleError, validate_sample

TEXT = PROMPTS["fixed_01"]


def make_sample(user="U01", session="S01", text=TEXT, hold=90.0, gap=70.0, valid=True):
    """Synthetic sample: each key held `hold` ms, up->down gap of `gap` ms."""
    events, t = [], 0.0
    for ch in text:
        events.append({"key": ch, "type": "down", "time_ms": t})
        events.append({"key": ch, "type": "up", "time_ms": t + hold})
        t += hold + gap
    return {"user_id": user, "session_id": session, "prompt_id": "fixed_01",
            "entered_text": text, "valid": valid, "events": events}


class DbCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db.init_db(self.path)

    def tearDown(self):
        os.remove(self.path)

    def seed(self, user="U01"):
        db.add_user(user, self.path)
        return db.create_session(user, "fixed_01", db_path=self.path)


class TestRoundTrip(DbCase):
    def test_save_and_load_identical_events(self):
        sid = self.seed()
        s = make_sample()
        smp_id = db.save_sample(sid, s, self.path)
        loaded = db.load_sample(smp_id, self.path)
        self.assertEqual(loaded["events"], s["events"])
        self.assertEqual(loaded["user_id"], "U01")
        self.assertEqual(loaded["session_id"], "S01")
        self.assertTrue(loaded["valid"])

    def test_session_numbering(self):
        db.add_user("U01", self.path)
        a = db.create_session("U01", "fixed_01", db_path=self.path)
        b = db.create_session("U01", "fixed_01", db_path=self.path)
        sid_b = db.save_sample(b, make_sample(session="S02"), self.path)
        self.assertNotEqual(a, b)
        self.assertEqual(db.load_sample(sid_b, self.path)["session_id"], "S02")

    def test_duplicate_user_rejected(self):
        db.add_user("U01", self.path)
        with self.assertRaises(ValueError):
            db.add_user("U01", self.path)

    def test_invalid_sample_stored_but_excluded_from_training(self):
        sid = self.seed()
        bad = make_sample(valid=False)
        bad["entered_text"] = "biometria202"
        bad["events"] = bad["events"][:-2]
        db.save_sample(sid, bad, self.path)
        db.save_sample(sid, make_sample(), self.path)
        self.assertEqual(len(db.get_user_samples("U01", only_valid=False, db_path=self.path)), 2)
        X, y, _ = db.get_samples_for_training(db_path=self.path)
        self.assertEqual(len(X), 1)


class TestAtomicity(DbCase):
    def test_rejected_sample_leaves_no_rows(self):
        sid = self.seed()
        s = make_sample()
        s["events"][5]["time_ms"] = -1.0          # bad value
        with self.assertRaises(SampleError):
            db.save_sample(sid, s, self.path)
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_failure_midway_rolls_back(self):
        sid = self.seed()
        s = make_sample()
        # passes validation but fails the events INSERT (NaN -> NOT NULL in SQLite)
        s["events"][3]["time_ms"] = float("nan")
        with self.assertRaises(Exception):
            db.save_sample(sid, s, self.path)
        with closing(sqlite3.connect(self.path)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)

    def test_sample_must_match_session(self):
        sid = self.seed()
        with self.assertRaises(SampleError):
            db.save_sample(sid, make_sample(user="U99"), self.path)
        with self.assertRaises(SampleError):
            db.save_sample(sid, make_sample(session="S07"), self.path)

    def test_foreign_keys_enforced(self):
        with self.assertRaises(ValueError):
            db.create_session("NIE_MA", "fixed_01", db_path=self.path)


class TestValidation(unittest.TestCase):
    def test_ok(self):
        validate_sample(make_sample())

    def test_wrong_text(self):
        s = make_sample()
        s["entered_text"] = "biometria2025"
        with self.assertRaises(SampleError):
            validate_sample(s)

    def test_missing_up(self):
        s = make_sample()
        del s["events"][-1]
        with self.assertRaises(SampleError):
            validate_sample(s)

    def test_up_without_down(self):
        s = make_sample()
        s["events"].insert(0, {"key": "x", "type": "up", "time_ms": 0.0})
        with self.assertRaises(SampleError):
            validate_sample(s)

    def test_time_decreasing(self):
        s = make_sample()
        s["events"][4]["time_ms"] = 1.0
        with self.assertRaises(SampleError):
            validate_sample(s)

    def test_unknown_event_type(self):
        s = make_sample()
        s["events"][0]["type"] = "press"
        with self.assertRaises(SampleError):
            validate_sample(s)


class TestFeatures(unittest.TestCase):
    def test_values_and_length(self):
        f = extract_features(make_sample(hold=90.0, gap=70.0)["events"], TEXT)
        n = len(TEXT)
        self.assertEqual(len(f), 3 * n - 2)
        self.assertEqual(len(f), len(feature_names(TEXT)))
        self.assertTrue(all(abs(v - 90.0) < 1e-9 for v in f[:n]))                 # H
        self.assertTrue(all(abs(v - 160.0) < 1e-9 for v in f[n:2 * n - 1]))       # DD
        self.assertTrue(all(abs(v - 70.0) < 1e-9 for v in f[2 * n - 1:]))         # UD

    def test_overlap_gives_negative_ud(self):
        # 'a' held 100 ms, 'b' pressed after 60 ms (overlap)
        ev = [{"key": "a", "type": "down", "time_ms": 0.0},
              {"key": "b", "type": "down", "time_ms": 60.0},
              {"key": "a", "type": "up", "time_ms": 100.0},
              {"key": "b", "type": "up", "time_ms": 150.0}]
        f = extract_features(ev, "ab")
        self.assertEqual(f, [100.0, 90.0, 60.0, -40.0])   # H1 H2 DD1 UD1

    def test_repeated_letters(self):
        f = extract_features(make_sample(text="aa")["events"], "aa")
        self.assertEqual(len(f), 4)


class TestTrainingExport(DbCase):
    def test_X_y_groups_and_csv(self):
        for u, hold in (("U01", 80.0), ("U02", 120.0)):
            db.add_user(u, self.path)
            for sess in (1, 2):
                sid = db.create_session(u, "fixed_01", db_path=self.path)
                for k in range(3):
                    db.save_sample(sid, make_sample(u, f"S{sess:02d}", hold=hold + k), self.path)
        X, y, g = db.get_samples_for_training(db_path=self.path)
        self.assertEqual((len(X), len(y), len(g)), (12, 12, 12))
        self.assertEqual(set(y), {"U01", "U02"})
        X1, _, _ = db.get_samples_for_training(session_numbers=[1], db_path=self.path)
        self.assertEqual(len(X1), 6)
        out = self.path + ".csv"
        try:
            self.assertEqual(db.export_csv(out, db_path=self.path), 12)
            with open(out, encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0][:3], ["user_id", "session_number", "H_1"])
            self.assertEqual(len(rows[0]), 2 + 3 * len(TEXT) - 2)
        finally:
            os.remove(out)


if __name__ == "__main__":
    unittest.main()
