"""核对盲测服务的公布申请、结果访问与候补接口。"""
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from blindtest import Exchange
from service import handler_for

ROOT = Path(__file__).resolve().parents[1]
AT = "2027-01-18T11:30:00+08:00"


def load_exchange():
    data = json.loads((ROOT / "fixtures" / "exchange.json").read_text(encoding="utf-8"))
    return Exchange(data)


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(load_exchange()))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def get(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urlencode(params)
        try:
            with urlopen(url) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as err:
            return err.code, json.loads(err.read())

    def post(self, path, payload):
        req = Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except HTTPError as err:
            return err.code, json.loads(err.read())

    def test_health(self):
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_suite_publication(self):
        status, body = self.get("/v1/publications", {"suite": "R-1", "at": AT})
        self.assertEqual(status, 200)
        self.assertTrue(body["sealed"])
        groups = {item["group"] for item in body["publishable"]}
        self.assertEqual(groups, {"EQ-1", "EQ-3", "EQ-6"})
        self.assertEqual([w["group"] for w in body["withheld"]], ["EQ-2"])

    def test_suite_publication_unknown(self):
        status, _ = self.get("/v1/publications", {"suite": "R-9", "at": AT})
        self.assertEqual(status, 404)

    def test_bad_at_rejected(self):
        status, _ = self.get("/v1/publications", {"suite": "R-1", "at": "2027-01-18 11:30"})
        self.assertEqual(status, 400)

    def test_post_publication_request_allowed(self):
        status, body = self.post("/v1/publications", {"team": "QING", "suite": "R-1", "at": AT})
        self.assertEqual(status, 200)
        self.assertTrue(body["allowed"])
        self.assertIsNone(body["verdict"])
        codes = {e["code"] for e in body["entries"]}
        self.assertNotIn("C-118", codes)

    def test_post_publication_request_denied(self):
        status, body = self.post("/v1/publications", {"team": "HE", "suite": "R-1", "at": AT})
        self.assertEqual(status, 403)
        self.assertFalse(body["allowed"])
        status, _ = self.post("/v1/publications", {"team": "HE", "suite": "R-2", "at": AT})
        self.assertEqual(status, 403)
        status, _ = self.post("/v1/publications", {"team": "QING", "suite": "R-2", "at": AT})
        self.assertEqual(status, 409)

    def test_post_publication_bad_payload(self):
        status, _ = self.post("/v1/publications", {"team": "QING"})
        self.assertEqual(status, 400)

    def test_results(self):
        status, body = self.get("/v1/results", {"team": "QING", "at": AT})
        self.assertEqual(status, 200)
        self.assertEqual([c["suite"] for c in body["comparisons"]], ["R-1"])
        self.assertEqual(body["opportunities"][0]["application"], "A-10")
        self.assertTrue(body["waiting"])

    def test_results_unknown_team(self):
        status, _ = self.get("/v1/results", {"team": "NOPE", "at": AT})
        self.assertEqual(status, 404)

    def test_pools(self):
        status, body = self.get("/v1/pools", {"at": AT})
        self.assertEqual(status, 200)
        pools = {p["pool"]: p for p in body["pools"]}
        self.assertEqual(pools["CAP-P"]["occupants"], ["A-10"])

    def test_pool_board(self):
        status, body = self.get("/v1/pools/CAP-P", {"at": AT})
        self.assertEqual(status, 200)
        self.assertEqual(body["occupants"][0]["status"], "review")
        self.assertEqual(body["occupants"][0]["members"], ["QING"])

    def test_pool_unknown(self):
        status, _ = self.get("/v1/pools/CAP-X", {"at": AT})
        self.assertEqual(status, 404)

    def test_application_review(self):
        status, body = self.get("/v1/applications/A-10", {"at": AT})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "review")
        self.assertEqual(body["evidence"][0]["prediction"], "P-10")

    def test_application_not_yet_submitted(self):
        status, _ = self.get("/v1/applications/A-12", {"at": AT})
        self.assertEqual(status, 404)

    def test_unknown_path(self):
        status, _ = self.get("/v1/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
