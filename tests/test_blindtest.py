"""核对盲测评审、公布与候补规则在交换资料上的解释。"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from blindtest import Exchange

ROOT = Path(__file__).resolve().parents[1]
ZONE = timezone(timedelta(hours=8))


def at(hour, minute=0):
    return datetime(2027, 1, 18, hour, minute, tzinfo=ZONE)


def load():
    return json.loads((ROOT / "fixtures" / "exchange.json").read_text(encoding="utf-8"))


class PredictionLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = Exchange(load())

    def test_late_generation_rejected(self):
        locked = self.ex.locked_prediction("QING", "R-1", at(12))
        self.assertEqual(locked["id"], "P-10")
        rejected = [p["id"] for p in self.ex.rejected_predictions("QING", "R-1", at(12))]
        self.assertEqual(rejected, ["P-14"])

    def test_public_catalog_not_blind_evidence(self):
        cov = self.ex.coverage("QING", "R-1", at(10, 30))
        entry = next(e for e in cov["entries"] if e["code"] == "C-119")
        self.assertFalse(entry["blind"])
        self.assertIn("目录已公开", entry["note"])
        self.assertEqual(cov["covered"], ["EQ-1", "EQ-2"])
        self.assertTrue(cov["qualified"])

    def test_correction_versions_traceable(self):
        self.assertEqual(self.ex.current_measurement("EQ-2", at(10, 30))["id"], "X-2")
        current = self.ex.current_measurement("EQ-2", at(11, 30))
        self.assertEqual(current["id"], "X-7")
        self.assertEqual(current["supersedes"], "X-2")
        versions = [v["id"] for v in self.ex.measurement_versions("EQ-2", at(11, 30))]
        self.assertEqual(versions, ["X-2", "X-7"])

    def test_correction_changes_coverage(self):
        self.assertTrue(self.ex.coverage("QING", "R-1", at(10, 30))["qualified"])
        cov = self.ex.coverage("QING", "R-1", at(11, 30))
        self.assertFalse(cov["qualified"])
        self.assertEqual(cov["coverage"], 1)
        entry = next(e for e in cov["entries"] if e["group"] == "EQ-2")
        self.assertTrue(entry["corrected_after_lock"])


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = Exchange(load())

    def test_open_suite_not_publishable(self):
        pub = self.ex.suite_publication("R-2", at(11, 30))
        self.assertFalse(pub["sealed"])
        self.assertEqual(pub["publishable"], [])
        self.assertEqual(len(pub["withheld"]), 3)

    def test_shared_group_withheld(self):
        pub = self.ex.suite_publication("R-1", at(11, 30))
        self.assertTrue(pub["sealed"])
        groups = {item["group"] for item in pub["publishable"]}
        self.assertEqual(groups, {"EQ-1", "EQ-3", "EQ-6"})
        withheld = {item["group"]: item["reason"] for item in pub["withheld"]}
        self.assertIn("EQ-2", withheld)
        self.assertIn("R-2", withheld["EQ-2"])
        self.assertEqual([r["receipt"] for r in pub["requests"]], ["L-3"])

    def test_all_rounds_sealed_publish_correction(self):
        pub = self.ex.suite_publication("R-1", at(15))
        values = {item["group"]: item["value"] for item in pub["publishable"]}
        self.assertEqual(values["EQ-2"], 108)
        versions = {item["group"]: [v["id"] for v in item["versions"]] for item in pub["publishable"]}
        self.assertEqual(versions["EQ-2"], ["X-2", "X-7"])


class ComparisonAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = Exchange(load())

    def test_invited_team_gets_partial_detail(self):
        detail = self.ex.team_comparison("QING", "R-1", at(11, 30))
        self.assertTrue(detail["allowed"])
        codes = {e["code"] for e in detail["entries"]}
        self.assertEqual(codes, {"C-117", "C-119", "C-120"})
        self.assertIsNone(detail["verdict"])
        self.assertEqual([w["code"] for w in detail["withheld"]], ["C-118"])
        entry = next(e for e in detail["entries"] if e["code"] == "C-119")
        self.assertFalse(entry["blind"])

    def test_withdrawn_team_denied(self):
        detail = self.ex.team_comparison("HE", "R-1", at(11, 30))
        self.assertFalse(detail["allowed"])
        self.assertEqual(detail["status"], 403)

    def test_other_team_denied(self):
        detail = self.ex.team_comparison("HE", "R-2", at(15))
        self.assertFalse(detail["allowed"])
        self.assertEqual(detail["status"], 403)
        self.assertIn("未受邀", detail["reason"])

    def test_open_suite_denied(self):
        detail = self.ex.team_comparison("QING", "R-2", at(11, 30))
        self.assertFalse(detail["allowed"])
        self.assertEqual(detail["status"], 409)

    def test_full_verdict_after_all_sealed(self):
        detail = self.ex.team_comparison("YUAN", "R-2", at(15))
        self.assertTrue(detail["allowed"])
        self.assertEqual(
            detail["verdict"], {"coverage": 2, "min_coverage": 2, "qualified": True}
        )


class ApplicationReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = Exchange(load())

    def test_withdrawn_member_evidence_excluded(self):
        review = self.ex.application_review("A-10", at(11, 30))
        self.assertEqual(review["members"], ["QING"])
        self.assertEqual(review["withdrawn"], ["HE"])
        self.assertEqual([e["member"] for e in review["evidence"]], ["QING"])
        self.assertFalse(review["qualified"])
        self.assertEqual(review["status"], "review")
        evidence = review["evidence"][0]
        self.assertEqual(evidence["prediction"], "P-10")
        self.assertEqual(evidence["rejected"], ["P-14"])
        self.assertTrue(evidence["re_evaluate"])
        self.assertTrue(evidence["qualification_changed"])

    def test_unaffected_candidate_continues(self):
        review = self.ex.application_review("A-12", at(15))
        self.assertEqual(review["status"], "candidate")
        evidence = review["evidence"][0]
        self.assertFalse(evidence["re_evaluate"])
        self.assertFalse(evidence["qualification_changed"])


class PoolBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = Exchange(load())

    def test_waitlist_before_confirmation_arrives(self):
        board = self.ex.pool_board("CAP-P", at(10, 50))
        self.assertEqual(board["occupants"], [])
        statuses = {e["application"]: e["status"] for e in board["waitlist"]}
        self.assertEqual(statuses, {"A-10": "promotable", "A-11": "waiting", "A-13": "waiting"})
        positions = {e["application"]: e["position"] for e in board["waitlist"]}
        self.assertEqual(positions, {"A-10": 1, "A-11": 2, "A-13": 3})

    def test_confirmed_seat_review_after_correction(self):
        board = self.ex.pool_board("CAP-P", at(11, 30))
        self.assertEqual(len(board["occupants"]), 1)
        occupant = board["occupants"][0]
        self.assertEqual(occupant["application"], "A-10")
        self.assertEqual(occupant["status"], "review")
        self.assertEqual(occupant["members"], ["QING"])
        self.assertEqual(occupant["withdrawn"], ["HE"])
        self.assertIn("有效覆盖", "; ".join(occupant["reasons"]))
        waiting = {e["application"]: e for e in board["waitlist"]}
        self.assertEqual(waiting["A-11"]["status"], "waiting")
        self.assertIn("复查", "; ".join(waiting["A-11"]["reasons"]))
        self.assertEqual(waiting["A-13"]["status"], "waiting")
        self.assertEqual(board["available"], 0)

    def test_alloy_pool_late_application(self):
        board = self.ex.pool_board("CAP-A", at(11, 30))
        self.assertEqual(board["occupants"], [])
        self.assertEqual(board["waitlist"], [])
        board = self.ex.pool_board("CAP-A", at(15))
        self.assertEqual(len(board["waitlist"]), 1)
        entry = board["waitlist"][0]
        self.assertEqual(entry["application"], "A-12")
        self.assertEqual(entry["status"], "promotable")


class TeamResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ex = Exchange(load())

    def test_buckets(self):
        view = self.ex.team_results("QING", at(11, 30))
        self.assertEqual([c["suite"] for c in view["comparisons"]], ["R-1"])
        pairs = [(o["application"], o["status"]) for o in view["opportunities"]]
        self.assertEqual(pairs, [("A-10", "review")])
        items = "; ".join(w["item"] for w in view["waiting"])
        self.assertIn("R-2", items)
        self.assertIn("EQ-2", items)

    def test_withdrawn_team_sees_no_detail(self):
        view = self.ex.team_results("HE", at(11, 30))
        self.assertIsNotNone(view["withdrawn"])
        self.assertEqual(view["comparisons"], [])
        self.assertTrue(any("退出" in w["reason"] for w in view["waiting"]))

    def test_late_round_pending_then_viewable(self):
        view = self.ex.team_results("YUAN", at(11, 30))
        self.assertEqual([c["suite"] for c in view["comparisons"]], ["R-1"])
        self.assertTrue(any("R-2" in w["item"] for w in view["waiting"]))
        view = self.ex.team_results("YUAN", at(15))
        self.assertEqual([c["suite"] for c in view["comparisons"]], ["R-1", "R-2"])
        self.assertEqual(view["opportunities"][0]["status"], "promotable")


class ConflictTests(unittest.TestCase):
    def test_member_cannot_hold_two_seats_with_same_suite(self):
        data = load()
        data["receipts"] = [r for r in data["receipts"] if r["id"] != "L-2"]
        ex = Exchange(data)
        board = ex.pool_board("CAP-P", at(11, 30))
        waiting = {e["application"]: e for e in board["waitlist"]}
        self.assertEqual(waiting["A-13"]["status"], "blocked")
        reasons = "; ".join(waiting["A-13"]["reasons"])
        self.assertIn("HE", reasons)
        self.assertIn("A-10", reasons)

    def test_withdrawal_after_confirmation_needs_clarification(self):
        data = load()
        for rec in data["receipts"]:
            if rec["id"] == "L-2":
                rec["happened"] = "2027-01-18T10:53:00+08:00"
                rec["arrived"] = "2027-01-18T11:05:00+08:00"
        ex = Exchange(data)
        review = ex.application_review("A-10", at(11, 30))
        self.assertEqual(review["members"], ["QING", "HE"])
        self.assertEqual(review["withdrawn"], [])
        self.assertTrue(any("确认后" in issue for issue in review["issues"]))
        self.assertEqual(review["status"], "review")

    def test_replaced_support_uses_locked_generation(self):
        data = load()
        for rec in data["predictions"]:
            if rec["id"] == "P-14":
                rec["entered"] = "2027-01-18T09:50:00+08:00"
        ex = Exchange(data)
        review = ex.application_review("A-10", at(11, 30))
        evidence = review["evidence"][0]
        self.assertEqual(evidence["prediction"], "P-14")
        self.assertTrue(any("P-14" in issue for issue in review["issues"]))


if __name__ == "__main__":
    unittest.main()
