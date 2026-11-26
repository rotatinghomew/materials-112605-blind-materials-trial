"""盲测评审、明细公布与中试席位候补的业务规则。

数据模型见 docs/exchange.md。本模块在同一份交换资料上提供三种解释:

* 公布申请: 轮次封闭后可公布套组明细; 与未封闭轮次同组的实测值及可反推
  差值暂缓公布, 已退出团队与未受邀团队不得查阅。
* 访问结果: 申请方区分已可查看的比较、批准的中试机会与仍在等待的项目。
* 候补过程: 评审按席位池核对占用人与证据; 实测更正后需重评的候选单独标出,
  已确认席位失去关键套组时进入复查, 不再把同一机会许给别家。

所有评估以 at 为时点: 只采纳 created/entered/submitted/arrived 不晚于 at
的记录; 回执的业务效力自 happened 起算, 到达时间不代替业务时间。
"""

from partner import parse_time

STATUS_LABELS = {
    "confirmed": "已确认",
    "review": "复查",
    "candidate": "候补合格",
    "unqualified": "不合格",
    "promotable": "可递补",
    "waiting": "等待",
    "blocked": "占位冲突",
}


class Exchange:
    """一份交换资料在指定时点的业务解释。"""

    def __init__(self, data):
        self.data = data
        self.groups = {rec["id"]: rec for rec in data["groups"]}
        self.suites = {rec["id"]: rec for rec in data["suites"]}
        self.teams = {rec["id"]: rec for rec in data["teams"]}
        self.predictions = {rec["id"]: rec for rec in data["predictions"]}
        self.measurements = {rec["id"]: rec for rec in data["measurements"]}
        self.slots = {rec["id"]: rec for rec in data["slots"]}
        self.applications = {rec["id"]: rec for rec in data["applications"]}
        self.receipts = list(data["receipts"])

    @staticmethod
    def _reached(text, at):
        return parse_time(text) <= at

    # -- 实测版本: 更正前后两版都可追溯 --
    def measurement_versions(self, group_id, at):
        versions = [
            rec for rec in self.measurements.values()
            if rec["group"] == group_id and self._reached(rec["created"], at)
        ]
        versions.sort(key=lambda rec: (parse_time(rec["created"]), rec["id"]))
        return versions

    def current_measurement(self, group_id, at):
        versions = self.measurement_versions(group_id, at)
        return versions[-1] if versions else None

    # -- 轮次封闭与目录公开 --
    def suite_sealed(self, suite_id, at):
        """轮次不再接受预测。"""
        return at > parse_time(self.suites[suite_id]["closes"])

    def suites_with_group(self, group_id):
        return [
            suite for suite in self.suites.values()
            if any(group == group_id for _, group in suite["samples"])
        ]

    def group_publishable(self, group_id, at):
        """目录已公开, 或涉及该组的所有轮次都不再接受预测。"""
        group = self.groups[group_id]
        if group["catalog_visible"] and self._reached(group["catalog_visible"], at):
            return True
        return all(
            self.suite_sealed(suite["id"], at)
            for suite in self.suites_with_group(group_id)
        )

    # -- 预测: 截止前最后一代为准, 截止后不得以补资料为由更换 --
    def team_predictions(self, team_id, suite_id, at):
        preds = [
            rec for rec in self.predictions.values()
            if rec["team"] == team_id and rec["suite"] == suite_id
            and self._reached(rec["entered"], at)
        ]
        preds.sort(key=lambda rec: (rec["generation"], parse_time(rec["entered"]), rec["id"]))
        return preds

    def locked_prediction(self, team_id, suite_id, at):
        closes = parse_time(self.suites[suite_id]["closes"])
        on_time = [
            rec for rec in self.team_predictions(team_id, suite_id, at)
            if parse_time(rec["entered"]) <= closes
        ]
        return on_time[-1] if on_time else None

    def rejected_predictions(self, team_id, suite_id, at):
        closes = parse_time(self.suites[suite_id]["closes"])
        return [
            rec for rec in self.team_predictions(team_id, suite_id, at)
            if parse_time(rec["entered"]) > closes
        ]

    # -- 覆盖: 满足轮次锁定误差线的独立盲样组数 --
    def coverage(self, team_id, suite_id, at):
        suite = self.suites[suite_id]
        closes = parse_time(suite["closes"])
        invited = suite_id in self.teams.get(team_id, {}).get("suites", [])
        pred = self.locked_prediction(team_id, suite_id, at)
        entries = []
        covered = []
        for code, group_id in suite["samples"]:
            entry = {"code": code, "group": group_id}
            group = self.groups[group_id]
            measurement = self.current_measurement(group_id, at)
            if measurement is not None:
                entry["version"] = measurement["id"]
                entry["corrected_after_lock"] = parse_time(measurement["created"]) > closes
            if pred is None or code not in pred["predictions"]:
                entry["note"] = "预测未覆盖"
            elif measurement is None:
                entry["note"] = "实测未登记"
            else:
                predicted = pred["predictions"][code]
                error = abs(predicted - measurement["value"])
                visible = group["catalog_visible"]
                # 实测值先公开的组别, 之后提交的预测不作盲测证据;
                # 同组其余代号也不算新的独立盲样。
                blind = not (visible and parse_time(visible) <= parse_time(pred["entered"]))
                entry.update({
                    "predicted": predicted,
                    "measured": measurement["value"],
                    "error": error,
                    "within": error <= suite["error"],
                    "blind": blind,
                })
                if not blind:
                    entry["note"] = "目录已公开, 不作盲测证据"
                if entry["within"] and blind and group_id not in covered:
                    covered.append(group_id)
            entries.append(entry)
        return {
            "team": team_id,
            "suite": suite_id,
            "invited": invited,
            "prediction": pred["id"] if pred else None,
            "rejected": [rec["id"] for rec in self.rejected_predictions(team_id, suite_id, at)],
            "entries": entries,
            "covered": covered,
            "coverage": len(covered),
            "min_coverage": suite["min_coverage"],
            "qualified": bool(pred) and invited and len(covered) >= suite["min_coverage"],
        }

    # -- 回执: arrived 不晚于 at 才为本地所知, 效力自 happened 起算 --
    def known_receipts(self, at):
        return [rec for rec in self.receipts if self._reached(rec["arrived"], at)]

    def withdrawals(self, at):
        """各团队最早一次退出请求的业务时刻。"""
        result = {}
        for rec in self.known_receipts(at):
            if rec["feedback"] == "withdraw" and rec["target"] in self.teams:
                happened = parse_time(rec["happened"])
                if rec["target"] not in result or happened < result[rec["target"]]:
                    result[rec["target"]] = happened
        return result

    def confirmations(self, at):
        """各申请获得中试方确认的业务时刻。"""
        result = {}
        for rec in self.known_receipts(at):
            if rec["feedback"] == "seat_confirmed" and rec["target"] in self.applications:
                happened = parse_time(rec["happened"])
                if rec["target"] not in result or happened < result[rec["target"]]:
                    result[rec["target"]] = happened
        return result

    def detail_requests(self, suite_id, at):
        return [
            {"receipt": rec["id"], "happened": rec["happened"]}
            for rec in self.known_receipts(at)
            if rec["feedback"] == "detail_requested" and rec["target"] == suite_id
        ]

    # -- 申请评审: 正式提交的组合与当时可用的证据 --
    def application_review(self, application_id, at):
        app = self.applications[application_id]
        slot = self.slots[app["pool"]]
        confirmed = self.confirmations(at).get(application_id)
        left = self.withdrawals(at)
        members, withdrawn, issues, conflicts = [], [], [], []
        for team_id in app["union"]:
            when = left.get(team_id)
            if when is not None and confirmed is not None and when >= confirmed:
                conflicts.append(f"成员 {team_id} 在邀请确认后请求退出, 需中试方明确是否可收回")
                members.append(team_id)
            elif when is not None:
                withdrawn.append(team_id)
                issues.append(f"成员 {team_id} 已退出, 其证据不计")
            else:
                members.append(team_id)
        issues.extend(conflicts)
        structural_ok = True
        evidence = []
        for pid in app["support"]:
            pred = self.predictions.get(pid)
            if pred is None:
                issues.append(f"证据 {pid} 不存在")
                structural_ok = False
                continue
            member, suite_id = pred["team"], pred["suite"]
            if member not in app["union"]:
                issues.append(f"证据 {pid} 的提交方 {member} 不在组合内")
                structural_ok = False
                continue
            if member in withdrawn:
                continue
            suite = self.suites.get(suite_id)
            if suite is None:
                issues.append(f"证据 {pid} 指向未知套组 {suite_id}")
                structural_ok = False
                continue
            item = {"member": member, "suite": suite_id, "support": pid, "qualified": True}
            if suite_id not in self.teams[member]["suites"]:
                issues.append(f"成员 {member} 未受邀 {suite_id}, 证据 {pid} 不作数")
                structural_ok = False
                item["qualified"] = False
            if suite["suite_family"] != slot["family"]:
                issues.append(
                    f"套组 {suite_id} 类别{suite['suite_family']}"
                    f"与席位 {slot['id']} 类别{slot['family']}不符"
                )
                structural_ok = False
                item["qualified"] = False
            locked = self.locked_prediction(member, suite_id, at)
            if locked is None:
                issues.append(f"成员 {member} 在 {suite_id} 无截止前预测")
                item.update({"prediction": None, "coverage_ok": False, "qualified": False})
            else:
                cov = self.coverage(member, suite_id, at)
                at_lock = self.coverage(member, suite_id, parse_time(suite["closes"]))
                item.update({
                    "prediction": locked["id"],
                    "coverage": cov["coverage"],
                    "min_coverage": cov["min_coverage"],
                    "coverage_ok": cov["qualified"],
                    "rejected": cov["rejected"],
                    "re_evaluate": any(e.get("corrected_after_lock") for e in cov["entries"]),
                    "qualification_changed": cov["qualified"] != at_lock["qualified"],
                    "qualified": item["qualified"] and cov["qualified"],
                })
                if locked["id"] != pid:
                    issues.append(f"证据 {pid} 以截止前最后版本 {locked['id']} 为准")
            evidence.append(item)
        member_ok = {}
        for team_id in members:
            items = [item for item in evidence if item["member"] == team_id]
            if not items:
                issues.append(f"成员 {team_id} 未提交支撑套组")
            # 组内有效覆盖不足的成员不能借用其他成员的结果
            member_ok[team_id] = bool(items) and all(item["qualified"] for item in items)
        size_ok = 1 <= len(members) <= slot["max_team_size"]
        if not size_ok:
            issues.append(f"有效成员 {len(members)} 人, 席位 {slot['id']} 至多允许 {slot['max_team_size']} 人")
        qualified = structural_ok and size_ok and bool(members) and all(member_ok.values())
        reasons = list(issues)
        for item in evidence:
            if item.get("prediction") and not item["coverage_ok"]:
                note = (
                    f"成员 {item['member']} 对 {item['suite']} "
                    f"有效覆盖 {item['coverage']} 未达 {item['min_coverage']}"
                )
                if item["re_evaluate"]:
                    note += "(实测更正后需重评)"
                reasons.append(note)
        if confirmed is not None:
            status = "confirmed" if qualified and not conflicts else "review"
        else:
            status = "candidate" if qualified else "unqualified"
        return {
            "id": application_id,
            "pool": app["pool"],
            "submitted": app["submitted"],
            "union": list(app["union"]),
            "members": members,
            "withdrawn": withdrawn,
            "max_team_size": slot["max_team_size"],
            "evidence": evidence,
            "issues": issues,
            "qualified": qualified,
            "confirmation": confirmed.isoformat() if confirmed else None,
            "status": status,
            "label": STATUS_LABELS[status],
            "reasons": reasons if status in ("review", "unqualified") else [],
            "at": at.isoformat(),
        }

    # -- 席位池候补: 按合格候补登记时刻排序, 同刻按申请号 --
    def pool_board(self, pool_id, at):
        slot = self.slots[pool_id]
        apps = [
            rec for rec in self.applications.values()
            if rec["pool"] == pool_id and self._reached(rec["submitted"], at)
        ]
        apps.sort(key=lambda rec: (parse_time(rec["submitted"]), rec["id"]))
        reviews = {rec["id"]: self.application_review(rec["id"], at) for rec in apps}
        confirmations = self.confirmations(at)
        occupants, waitlist = [], []
        for rec in apps:
            review = reviews[rec["id"]]
            entry = {
                "application": rec["id"],
                "submitted": rec["submitted"],
                "members": review["members"],
                "withdrawn": review["withdrawn"],
                "qualified": review["qualified"],
                "evidence": review["evidence"],
                "issues": review["issues"],
            }
            if rec["id"] in confirmations:
                entry["confirmed"] = confirmations[rec["id"]].isoformat()
                entry["status"] = review["status"]
                entry["label"] = STATUS_LABELS[review["status"]]
                entry["reasons"] = review["reasons"]
                occupants.append(entry)
            else:
                waitlist.append(entry)
        occupants.sort(key=lambda entry: (entry["confirmed"], entry["application"]))
        # 已占用正式邀请的成员不能用同一套组在另一个组合同时占位
        holders = {}
        for occupant in occupants:
            for item in occupant["evidence"]:
                holder = holders.setdefault(
                    item["member"], {"application": occupant["application"], "suites": set()}
                )
                holder["suites"].add(item["suite"])
        free = slot["capacity"] - len(occupants)
        for position, entry in enumerate(waitlist, 1):
            entry["position"] = position
            block = None
            for item in entry["evidence"]:
                holder = holders.get(item["member"])
                if holder and item["suite"] in holder["suites"]:
                    block = (
                        f"成员 {item['member']} 已在 {holder['application']} "
                        f"以 {item['suite']} 占用正式邀请"
                    )
                    break
            if block:
                entry["status"] = "blocked"
                entry["reasons"] = [block]
            elif not entry["qualified"]:
                entry["status"] = "unqualified"
                entry["reasons"] = reviews[entry["application"]]["reasons"]
            elif free > 0:
                entry["status"] = "promotable"
                free -= 1
                entry["reasons"] = ["席位未被认领, 可按登记顺序递补"]
            else:
                entry["status"] = "waiting"
                names = "、".join(o["application"] for o in occupants)
                if any(o["status"] == "review" for o in occupants):
                    entry["reasons"] = [f"席位 {names} 复查中, 为避免重复许诺暂不递补"]
                elif occupants:
                    entry["reasons"] = [f"席位已由 {names} 占用"]
                else:
                    entry["reasons"] = ["递补名额已按登记顺序排满"]
            entry["label"] = STATUS_LABELS[entry["status"]]
        promotable = sum(1 for entry in waitlist if entry["status"] == "promotable")
        return {
            "pool": pool_id,
            "family": slot["family"],
            "capacity": slot["capacity"],
            "max_team_size": slot["max_team_size"],
            "available": slot["capacity"] - len(occupants) - promotable,
            "occupants": occupants,
            "waitlist": waitlist,
            "at": at.isoformat(),
        }

    # -- 公布: 先封闭的先公布, 与未封闭轮次同组的暂缓 --
    def suite_publication(self, suite_id, at):
        suite = self.suites[suite_id]
        sealed = self.suite_sealed(suite_id, at)
        publishable, withheld = [], []
        for code, group_id in suite["samples"]:
            if not sealed:
                withheld.append({
                    "code": code,
                    "group": group_id,
                    "reason": "轮次仍接受预测, 截止后方可公布",
                })
                continue
            if not self.group_publishable(group_id, at):
                open_rounds = [
                    s["id"] for s in self.suites_with_group(group_id)
                    if not self.suite_sealed(s["id"], at)
                ]
                withheld.append({
                    "code": code,
                    "group": group_id,
                    "reason": f"与未封闭轮次 {'、'.join(open_rounds)} 同组, 实测值及可反推差值暂缓公布",
                })
                continue
            measurement = self.current_measurement(group_id, at)
            if measurement is None:
                withheld.append({"code": code, "group": group_id, "reason": "实测未登记"})
                continue
            publishable.append({
                "code": code,
                "group": group_id,
                "value": measurement["value"],
                "version": measurement["id"],
                "created": measurement["created"],
                "supersedes": measurement["supersedes"],
                "versions": [
                    {
                        "id": v["id"],
                        "value": v["value"],
                        "created": v["created"],
                        "supersedes": v["supersedes"],
                    }
                    for v in self.measurement_versions(group_id, at)
                ],
            })
        return {
            "suite": suite_id,
            "closes": suite["closes"],
            "sealed": sealed,
            "publishable": publishable,
            "withheld": withheld,
            "requests": self.detail_requests(suite_id, at),
            "at": at.isoformat(),
        }

    # -- 公布申请: 申请方查看自己的比较明细 --
    def team_comparison(self, team_id, suite_id, at):
        team = self.teams[team_id]
        if suite_id not in team["suites"]:
            return {"allowed": False, "status": 403, "reason": f"团队 {team_id} 未受邀轮次 {suite_id}"}
        if team_id in self.withdrawals(at):
            return {"allowed": False, "status": 403, "reason": f"团队 {team_id} 已退出, 实测明细不再提供"}
        if not self.suite_sealed(suite_id, at):
            return {"allowed": False, "status": 409, "reason": f"轮次 {suite_id} 仍接受预测, 截止后方可公布"}
        publication = self.suite_publication(suite_id, at)
        cov = self.coverage(team_id, suite_id, at)
        by_code = {entry["code"]: entry for entry in cov["entries"]}
        entries = []
        for item in publication["publishable"]:
            entry = {
                "code": item["code"],
                "group": item["group"],
                "measured": item["value"],
                "version": item["version"],
            }
            mine = by_code.get(item["code"], {})
            if "predicted" in mine:
                entry.update({
                    "predicted": mine["predicted"],
                    "error": mine["error"],
                    "within": mine["within"],
                    "blind": mine["blind"],
                })
                if not mine["blind"]:
                    entry["note"] = mine["note"]
            else:
                entry["note"] = "预测未覆盖"
            entries.append(entry)
        result = {
            "allowed": True,
            "team": team_id,
            "suite": suite_id,
            "prediction": cov["prediction"],
            "rejected": cov["rejected"],
            "entries": entries,
            "withheld": publication["withheld"],
            "at": at.isoformat(),
        }
        if publication["withheld"]:
            # 暂缓组别的实测值、误差与合格结论一并压住, 避免可反推的差值泄漏
            result["verdict"] = None
            result["note"] = "含暂缓公布组别, 合格结论待相关轮次全部封闭后提供"
        else:
            result["verdict"] = {
                "coverage": cov["coverage"],
                "min_coverage": cov["min_coverage"],
                "qualified": cov["qualified"],
            }
        return result

    # -- 访问结果: 可查看的比较、批准的机会、等待的项目 --
    def team_results(self, team_id, at):
        team = self.teams[team_id]
        left = self.withdrawals(at).get(team_id)
        comparisons, opportunities, waiting = [], [], []
        for suite_id in team["suites"]:
            if not self.suite_sealed(suite_id, at):
                waiting.append({
                    "item": f"轮次 {suite_id} 比较",
                    "reason": f"轮次仍接受预测至 {self.suites[suite_id]['closes']}",
                })
                continue
            if left is not None:
                waiting.append({
                    "item": f"轮次 {suite_id} 比较",
                    "reason": "团队已退出, 实测明细不再提供",
                })
                continue
            detail = self.team_comparison(team_id, suite_id, at)
            comparisons.append(detail)
            for item in detail["withheld"]:
                waiting.append({
                    "item": f"轮次 {suite_id} 组别 {item['group']}",
                    "reason": item["reason"],
                })
        boards = {}
        for app in sorted(self.applications.values(), key=lambda rec: rec["id"]):
            if team_id not in app["union"] or not self._reached(app["submitted"], at):
                continue
            pool = app["pool"]
            if pool not in boards:
                boards[pool] = self.pool_board(pool, at)
            board = boards[pool]
            entry = next(
                (o for o in board["occupants"] if o["application"] == app["id"]), None
            )
            if entry is None:
                entry = next(
                    (w for w in board["waitlist"] if w["application"] == app["id"]), None
                )
            if entry is None:
                continue
            item = {
                "application": app["id"],
                "pool": pool,
                "status": entry["status"],
                "label": entry["label"],
                "members": entry["members"],
            }
            if team_id in entry["withdrawn"]:
                item["member_withdrawn"] = True
            if entry["status"] in ("confirmed", "review", "promotable"):
                if "confirmed" in entry:
                    item["confirmed"] = entry["confirmed"]
                if "position" in entry:
                    item["position"] = entry["position"]
                if entry.get("reasons"):
                    item["reasons"] = entry["reasons"]
                opportunities.append(item)
            else:
                waiting.append({
                    "item": f"申请 {app['id']}(席位 {pool})",
                    "reason": "; ".join(entry["reasons"]),
                })
        return {
            "team": team_id,
            "withdrawn": left.isoformat() if left else None,
            "comparisons": comparisons,
            "opportunities": opportunities,
            "waiting": waiting,
            "at": at.isoformat(),
        }
