# Trade Pricing Engine · 外贸报价与利润决策引擎

**独立服务 / v0.2.0 / 可本地验收，尚未经真实企业试运营。**

面向中国出口企业：把一条真实询盘变成有成本依据、可复算、可比较、须独立审核的销售报价。它不是供应商搜索器、ERP、自动退税计算器，也不是让大模型自行决定底价。

正式 GitHub 仓库：[magicy565-web/trade-pricing-engine](https://github.com/magicy565-web/trade-pricing-engine)。已于 2026-11-11 通过授权 GitHub 接口发布独立源码，首个完整源码提交为 `5c31c6cdd7df56c2017a9e8dc05f365794af8e13`。原本地版本的 Git 文件树 SHA `cd18142e11cfcb50a4f68cb059da68719ff0e57c` 与该发布提交的文件树完全一致（78 个文件）。

## 已实现

| 能力 | 实际交付 |
|---|---|
| 精确核算 | Decimal；金额只接受字符串；按单位/整单成本、收入比例费用、显式汇率快照 |
| 价格决策 | 目标报价、政策底价、保本价、议价空间、订单贡献额/贡献率；不是会计净利润 |
| 证据与范围 | 产品修订、数量范围、计量单位、交货地点、运输方式、付款条件、有效期、源文件哈希 |
| 费用完整性 | EXW/FCA/FOB/CIF/DAP 的保守检查清单；打包费用与单列费用冲突检查 |
| 方案比较 | 显式数量、价格、贸易条款和付款条件方案；不同条件必须有对应确认成本，不猜阶梯折扣 |
| 现金占用 | 显式收支日程的最大资金缺口和 ACT/365 单利融资估算；不自动计入报价，避免重复 |
| 审批 | 草稿→待审→批准→内部定稿；内容哈希、版本锁、角色隔离、源变更/过期阻断 |
| 对客文件 | 经过允许字段清单过滤的英文 HTML 报价；不含采购成本、利润底线、内部证据 |
| 审计与恢复 | SQLite 事务、请求去重、审计哈希链、重启恢复、历史计算重放 |
| 接口与页面 | FastAPI HTTP、15 个 MCP stdio 工具、中文响应式操作台 |

**不存在的能力也明确：** 没有真实买家数据或虚构生产数据；没有自动抓取运费/汇率/关税；没有自动退税；没有自动外发、开票、收款或下单；没有真实 OpenMausBot 宿主验收。DDP 可记录需求，但 v0.1 明确阻断正式报价，不能以几个关税字段冒充进口合规已解决。

## 技术选择

本项目最终使用 **Python 3.11+ + Decimal + SQLite + FastAPI**，不是此前讨论的 TypeScript 方案。原因是精确金额核算直接使用成熟的标准库，并能在本次环境中真实执行整套测试。OpenMausBot 通过进程/HTTP 边界调用，不依赖宿主语言，也不增加 Agent Runtime 或模型密钥。

v0.2 本轮验证运行环境：Python 3.12.14 / Linux；v0.1 曾验证 Python 3.13.5 / Linux。Windows 与其他 Python 版本尚未实机验收；CI 配置不是已通过的 CI 证据。

## 快速启动

从源码目录执行：

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: . .venv/bin/activate
python -m pip install -e ".[test]"
python -m trade_pricing init --directory private --tenant my-company --seller "你的真实企业名称"
python -m trade_pricing serve --config private/server.json
```

浏览器打开 `http://127.0.0.1:8875`。启动只创建空库，不注入演示客户或价格。初始化命令拒绝覆盖非空目录。

选择角色密钥文件进入操作台：

| 文件 | 用途 | 不应交给谁 |
|---|---|---|
| `private/admin.token` | 确认成本、汇率和企业政策；撤销过期依据 | Worker / 买家 |
| `private/agent.token` | 创建案件、计算、比较、提交审核 | 买家 |
| `private/approver.token` | 独立批准、驳回、内部定稿 | Worker / 自动化脚本（生产环境） |
| `private/viewer.token` | 企业内部只读 | 买家 |

**这些都是企业内部凭据，不存在“买家访问整个操作台”的权限。** 密钥仅在页面内存保存，刷新后需重新连接。不要把整个 `private/` 放进 Git 或给具备主机 Shell 权限的 Worker。

## 第一次真实报价

1. 业务员/Worker 根据 `schemas/CaseInput.json` 建立询盘；网页支持导入该 JSON。
2. 成本管理员依据原文件确认 `CostBook`、`FxBook`、`Policy` 三类快照。单币种也要确认一个空 `rates` 的同币种 FX 快照，不能把空数组理解为缺失外币可以忽略。
3. 选取对应快照，生成报价版本。成本缺失或不适用时只有阻断理由，没有伪装完整的利润数字。
4. 提交审核；审批员使用自己的凭据批准特定内容哈希。修改案件、撤销依据、变更政策或到期后不能沿用旧审批。同案件修订下单独核算新价格保留独立报价；若用于替代旧报价，由审批员显式撤回旧版。
5. 下载带标识的批准草稿，或由审批员内部定稿后导出。**`issued` 只表示内部定稿，`delivery_status` 始终为 `NOT_SENT`。** 发送应由现有渠道系统独立确认。

网页当前是**结构化 JSON 导入的初版操作台**，不是完整 Excel/PDF 抽取产品。自动提取和自然语言理解应由宿主 Worker 完成，提取结果仍要经过成本管理员确认。

## 接入 OpenMausBot / 其他 stdio MCP 宿主

先启动报价服务，然后注册独立进程：

```sh
python -m trade_pricing mcp --url http://127.0.0.1:8875 --token-file /private-agent-only/agent.token
```

使用已安装本项目的 Python 解释器，必要时配置宿主工作目录。详细接入规则见 `docs/OPENMAUSBOT.md` 和 `skills/trade-pricing/SKILL.md`。

这是 **2025-11-25 tools-only stdio 兼容接口**；同时协商 2025-06-18、2025-03-26、2024-11-05。不是最新 2026 MCP 全功能实现，不提供远程 Streamable HTTP/OAuth/采样/任务扩展。底层 FastAPI HTTP 接口不是直接可安装到 ChatGPT 的 OAuth MCP 服务。

## 验证

```sh
python -m pytest -q
python scripts/smoke.py
python scripts/export_schemas.py
```

- v0.2 本轮：**128 项 pytest 通过，0 失败、0 跳过**，比原版增加 26 项回归检查；覆盖分页、方案校验、并发选定、事务回滚、重新审批和撤回历史。
- 实际 HTTP 与 MCP 子进程联调：**10 项检查通过**，包括 MCP 比较→选定→修改→重新报价、审批定稿、进程重启、撤回后禁止导出及历史重算。
- JavaScript 语法检查通过。新增 `scripts/browser_smoke.py` 用于真实浏览器全流程验收；本轮 Chromium 启动被运行环境禁止创建套接字（`Operation not permitted`），**没有通过浏览器完整 E2E，也没有重新验证 v0.2 的视觉布局**。
- v0.1 的 `verification/ui-*` 截图及旧报告属于历史证据，不代表 v0.2 页面验收。
- 全部业务夹具均为合成资料，不代表真实市场价格、企业财务复核或订单；没有联系任何外部买家。

本轮证据见 `verification/v0.2.0.json`、`verification/live-smoke-v0.2.0.json`；边界见 `docs/VERIFICATION.md`。

## v0.2 原案件闭环

- 网页可导入修订后的询盘 JSON，或将多个已确认成本条件加入比较，再选定方案生成新草稿。
- `promote_pricing_scenario` 在同一事务内重新校验依据、更新案件版本并创建报价；任何阻断都会回滚，不修改原案件。新报价必须重新审批，旧案件版本的报价不能再导出。
- `revise_pricing_case` 支持 Worker 使用版本锁修改原案件；比较接口对无效输入返回 422。
- 案件和快照列表支持游标分页；快照可按类型与是否撤销筛选，并可独立读取当前政策或指定快照。网页会读取后续页，不再截断到最近 300 条。
- 对客 JSON / HTML 包含产品修订号。审批员可撤回报价，记录理由、操作者和内容哈希；已下载文件需由企业另行通知接收方，系统不会远程回收或发送消息。
- 应用版本升级为 0.2.0，核算算法版本继续保留 0.1.0；本轮未改变金额公式与舍入规则，原历史报价仍可重算。无需数据库结构迁移，撤回动作记入既有决策记录表。


## GitHub 发布与校验

- 正式仓库：https://github.com/magicy565-web/trade-pricing-engine
- 2026-11-11 已发布完整初版 78 个文件，包含应用源码、MCP、Schema、测试和验证截图。后续文档提交不会更改最初完整源码提交的内容哈希。
- GitHub Actions 已触发，但首次运行 [#42962695257](https://github.com/magicy565-web/trade-pricing-engine/actions/runs/42962695257) 返回失败；4 个 job 均未返回可用执行步骤，无法由此认定代码测试失败或通过。Linux 本地 102 项测试与 8 项进程联调的证据仍见 `verification/`。
- **当前仓库为公开可见，但没有授予开源许可证**。项目所有者如希望其他开发者使用、修改或分发，应明确选择许可证。公开可读不等于开放授权。
- `scripts/publish_github.py` 保留为**新建私有仓库**的安全发布辅助脚本；它会拒绝向已存在的公开仓库执行发布，请不要在本仓库重复运行。

## 目录

```text
trade_pricing/core/        精确计算、费用检查、现金流
trade_pricing/models.py    严格输入契约
trade_pricing/store.py     SQLite、幂等、审计
trade_pricing/service.py   业务状态、审批、快照、历史重放
trade_pricing/api.py       受认证 HTTP API
trade_pricing/adapters/    非特权 stdio MCP
trade_pricing/web/         中文操作台
schemas/                  从运行模型生成的 24 份 JSON Schema
skills/trade-pricing/      Worker 操作技能
scripts/                  发布、备份、验收、Schema 导出
tests/                   隔离测试与显式合成夹具
```

后续设计见 `docs/ARCHITECTURE.md`；API 与核算口径见 `docs/API.md`；部署、安全、恢复见 `docs/SECURITY.md`。本仓库当前公开可读，但尚未授予任何开源许可证；对外授权和许可证选择由项目所有者决定。

