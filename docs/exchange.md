# 盲测交换

预测和实测单位均为 MPa。套组按申请时约定的项目核定，误差取绝对值。min_coverage 为套组中满足当前误差线的独立试件组数。同一互推关系值覆盖各轮次；某组可公开的前提是涉及该组的所有轮次均不再接受预测。席位按合格候补登记时刻排序，同刻按申请号。邀请必须在到期前由中试方确认。企业可撤回尚未确认的邀请。

资料使用 UTF-8 JSON。每个集合各自维护记录 id，跨集合引用使用相应记录 id。事件中的 happened 是业务发生时间，arrived 是对接方可提供该回执的时间。到达时间不能代替业务时间。这些记录来自不同流程阶段，读到一个回执并不代表本地已做过相应操作。

## `groups`

互推关系、所属材料族和目录已公开时间。字段为 `id`, `family`, `catalog_visible`。

## `suites`

独立轮次的套组和合格要求。字段为 `id`, `closes`, `min_coverage`, `error`, `suite_family`, `samples`。

## `teams`

受邀方和允许参加的套组。字段为 `id`, `suites`。

## `predictions`

每次完整提交的匿名预测，数字与匿名号对应。字段为 `id`, `team`, `suite`, `generation`, `entered`, `predictions`。

## `measurements`

由保管方按实物组记录的测量及可查时间。字段为 `id`, `group`, `value`, `created`, `supersedes`。

## `slots`

由中试站提供的席位及成员人数限制。字段为 `id`, `family`, `capacity`, `max_team_size`。

## `applications`

联合申请的成员、证据、排序时点与需求席位。union 含所有成员。字段为 `id`, `pool`, `union`, `support`, `submitted`。

## `receipts`

中试方反馈。feedback 区分确认、退出、请求公开细目。字段为 `id`, `target`, `feedback`, `happened`, `arrived`。

## 回执查询

本机回放只提供对接方的既有回执。`GET /v1/receipts?at=...` 的 `at` 是 URL 编码后的带偏移时间，返回对象中 `receipts` 是在该时间之前及当时可取得的回执。同一条数据可以在连续查询中出现，列表先后不代表业务先后。`503` 表示当前无法查询回执。

指定 `--journal` 时，`POST /v1/dispatches` 接收本地联调请求并登记原文。接收只代表到达对手方，不会生成履约回执。指定 `--drop-reply` 可在接收后断开。请求正文由业务客户端提供，回执仍由交换资料独立给出。
