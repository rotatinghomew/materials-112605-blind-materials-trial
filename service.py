"""盲测公布申请、结果访问与候补评审服务。

在交换资料(见 docs/exchange.md)上提供:

* `GET  /v1/publications?suite=...&at=...`  套组公布状态(保管方/评审视角)
* `POST /v1/publications`                   公布申请, 正文 {"team", "suite", "at"?}
* `GET  /v1/results?team=...&at=...`        申请方访问结果
* `GET  /v1/pools?at=...`                   席位池一览
* `GET  /v1/pools/{pool}?at=...`            候补过程(评审核对占用人与证据)
* `GET  /v1/applications/{id}?at=...`       单个申请的证据核对

`at` 是 URL 编码后的带偏移时间, 缺省取当前时刻。
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from blindtest import Exchange
from partner import parse_time

DATA_PATH = Path(__file__).with_name("fixtures") / "exchange.json"


def handler_for(exchange):
    class ServiceHandler(BaseHTTPRequestHandler):
        def reply(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def evaluation_time(self, query):
            values = query.get("at", [])
            if not values:
                return datetime.now(timezone.utc)
            if len(values) != 1:
                raise ValueError
            return parse_time(values[0])

        def do_GET(self):
            target = urlsplit(self.path)
            query = parse_qs(target.query)
            try:
                at = self.evaluation_time(query)
            except (ValueError, TypeError):
                self.reply(400, {"message": "请提供一个带时区的 at 参数"})
                return
            if target.path == "/health":
                self.reply(200, {"status": "ok"})
                return
            if target.path == "/v1/publications":
                suite_id = query.get("suite", [None])[0]
                if suite_id not in exchange.suites:
                    self.reply(404, {"message": "套组不存在"})
                    return
                self.reply(200, exchange.suite_publication(suite_id, at))
                return
            if target.path == "/v1/results":
                team_id = query.get("team", [None])[0]
                if team_id not in exchange.teams:
                    self.reply(404, {"message": "团队不存在"})
                    return
                self.reply(200, exchange.team_results(team_id, at))
                return
            if target.path == "/v1/pools":
                pools = []
                for pool_id in sorted(exchange.slots):
                    board = exchange.pool_board(pool_id, at)
                    pools.append({
                        "pool": pool_id,
                        "family": board["family"],
                        "capacity": board["capacity"],
                        "occupants": [o["application"] for o in board["occupants"]],
                        "waiting": len(board["waitlist"]),
                    })
                self.reply(200, {"pools": pools, "at": at.isoformat()})
                return
            if target.path.startswith("/v1/pools/"):
                pool_id = target.path[len("/v1/pools/"):]
                if pool_id not in exchange.slots:
                    self.reply(404, {"message": "席位不存在"})
                    return
                self.reply(200, exchange.pool_board(pool_id, at))
                return
            if target.path.startswith("/v1/applications/"):
                app_id = target.path[len("/v1/applications/"):]
                app = exchange.applications.get(app_id)
                if app is None or parse_time(app["submitted"]) > at:
                    self.reply(404, {"message": "申请不存在或该时点尚未提交"})
                    return
                self.reply(200, exchange.application_review(app_id, at))
                return
            self.reply(404, {"message": "路径不存在"})

        def do_POST(self):
            target = urlsplit(self.path)
            if target.path != "/v1/publications":
                self.reply(404, {"message": "路径不存在"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, UnicodeError):
                self.reply(400, {"message": "请求须为 JSON 对象"})
                return
            team_id = payload.get("team")
            suite_id = payload.get("suite")
            if not isinstance(team_id, str) or not isinstance(suite_id, str):
                self.reply(400, {"message": "请提供 team 与 suite"})
                return
            try:
                at = parse_time(payload["at"]) if "at" in payload else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                self.reply(400, {"message": "at 须为带时区的时间"})
                return
            if team_id not in exchange.teams:
                self.reply(404, {"message": "团队不存在"})
                return
            if suite_id not in exchange.suites:
                self.reply(404, {"message": "套组不存在"})
                return
            result = exchange.team_comparison(team_id, suite_id, at)
            if not result["allowed"]:
                self.reply(result["status"], {"allowed": False, "message": result["reason"]})
                return
            self.reply(200, result)

        def log_message(self, format, *args):
            pass

    return ServiceHandler


def main():
    parser = argparse.ArgumentParser(description="盲测公布申请、结果访问与候补评审服务")
    parser.add_argument("--port", type=int, default=8088, help="回环地址端口, 0 表示自动分配")
    parser.add_argument("--data", type=Path, default=DATA_PATH, help="只读取的交换资料")
    args = parser.parse_args()

    exchange = Exchange(json.loads(args.data.read_text(encoding="utf-8")))
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(exchange)) as server:
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
