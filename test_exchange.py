"""核对交换资料中的实体引用与原始回执顺序。"""
from datetime import datetime
import json
from pathlib import Path
import unittest

from partner import parse_time as time

ROOT = Path(__file__).resolve().parent


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "exchange.json").read_text(encoding="utf-8"))

    def test_record_identity(self):
        for name, collection in self.data.items():
            with self.subTest(collection=name):
                ids = [rec["id"] for rec in collection]
                self.assertTrue(ids)
                self.assertTrue(all(isinstance(i, str) and i for i in ids))
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(set(x) == set(collection[0]) for x in collection))

    def test_receipt_clocks(self):
        for rec in self.data["receipts"]:
            with self.subTest(receipt=rec["id"]):
                self.assertLessEqual(time(rec["happened"]), time(rec["arrived"]))
        with self.assertRaises(ValueError):
            time(datetime(2027, 1, 18).isoformat())

    def test_related_records(self):
        def row(collection, key):
            return next(rec for rec in self.data[collection] if rec["id"] == key)
        r1 = row("suites","R-1")
        r2 = row("suites","R-2")
        assert set(dict(r1["samples"])).isdisjoint(dict(r2["samples"]))
        assert set(dict(r1["samples"]).values()) & set(dict(r2["samples"]).values()) == {"EQ-2"}
        assert time(r1["closes"]) < time(row("receipts","L-3")["happened"]) < time(r2["closes"])
        assert row("measurements","X-7")["supersedes"] == "X-2"
        assert row("measurements","X-7")["group"] == row("measurements","X-2")["group"]
        assert set(row("applications","A-10")["union"]) & set(row("applications","A-13")["union"]) == {"HE"}
        assert row("applications","A-10")["pool"] == row("applications","A-11")["pool"]
        assert time(row("receipts","L-1")["happened"]) < time(row("measurements","X-7")["created"]) < time(row("receipts","L-1")["arrived"])
