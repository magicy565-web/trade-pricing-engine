# v0.2 本轮增量验收（2026-11-11）

- 基线：GitHub main `46d845f94b3884cae1954f4d9d9932bea04b7bd2`。
- Linux / Python 3.12.14；使用 requirements-dev.txt 指定的直接依赖。
- pytest：128 项通过，0 失败，0 跳过；有一条 Starlette/AnyIO 弃用警告。
- 实际 HTTP 与 MCP 子进程：10 项检查通过，包括新增比较、原子选定、修改案件、重报价、审批定稿、重启、撤回与重算；业务数据全部为合成夹具。
- JavaScript `node --check` 通过；从运行模型导出 24 个 Schema。
- 实际尝试 `scripts/browser_smoke.py`，Chromium 在创建进程单实例套接字时遭到环境拒绝（Operation not permitted），尚未到达页面加载。不得将此记录视为 v0.2 浏览器 E2E 或视觉布局通过。
- 新回归覆盖：零数量 / FCA 变体 / 分数件数错误返回 422；超过 300 快照仍可分页发现有效 FX/政策；并发选定仅生成一次；选定失败完整回滚；租户权限；对客产品修订；撤回后的导出阻断、理由记录与重启恢复。
- 没有真实供应商表单接入、真实企业财务确认、真实 OpenMausBot 宿主或外部发送验收。

真实浏览器脚本需要单独安装 Playwright 并提供 Chromium：`python scripts/browser_smoke.py`，可用 CHROMIUM_PATH 指定浏览器二进制。脚本会启动临时服务、生成隔离测试凭据、调用真实 HTTP、操作网页，不使用 fetch/路由 mock；成功才写通过报告。当前运行环境没有通过该项。

以下为 v0.1 历史记录，不可当作 v0.2 新页面的验收结果。

# 验证记录与未完成边界

## 本次实际执行

- Python 3.13.5 / Linux，FastAPI 0.128.2、Pydantic 2.13.5、Uvicorn 0.48.0、pytest 9.0.2。
- `python -m pytest -q --junitxml=verification/pytest.xml`：102 项通过，零失败、零跳过。
- `python scripts/smoke.py`：实际启动独立服务进程，使用真实本地 HTTP 请求；启动独立 MCP stdio 子进程完成初始化、工具列表和案件读取；实际执行审批角色的状态变化、校验审计、重启进程读取已保存状态。8 项检查通过。
- Decimal 数值回归包括 3 类币种、6 类数量、5 类成本和 6 类目标率的参数扫描；这些是确定性算法测试，不是利润保证或市场研究。
- Chromium 离线组件渲染：1440×1100 与 390×844，没有横向溢出或脚本错误。截图清楚标为合成数据。
- JSON Schema 从 Pydantic 运行模型导出，20 个合同文件。

## 浏览器限制

直接浏览器访问本地服务被当前环境的管理员策略拦截，返回 `ERR_BLOCKED_BY_ADMINISTRATOR`。没有修改或绕过该策略。改为读取自有页面源码并进行**不访问网络**的组件渲染；因此只报告 UI 组件渲染结果，不报告浏览器完整 E2E 成功。

## 数据来源

全部测试的客户、产品、成本、汇率/费用与审批操作都是显式合成夹具。服务进程联调中的“审批角色”由测试脚本持对应测试令牌调用，不等于真实财务人员完成商业审核。测试数据库/凭据均位于临时目录，交付包不含这些凭据或数据库。应用初始化不会把测试夹具导入生产。

## 没有完成

GitHub Actions 成功运行、真实 OpenMausBot 宿主连接、Windows 实机运行、真实企业财务验收、真实渠道发送、外部访问与部署、独立渗透测试、依赖漏洞扫描、第三方税务/关税/物流数据接入。

公开演示或正式销售之前应先选一家企业，用真实询盘与财务资料复核一轮。不要把“本地测试通过”换成“已验证外贸增收”。

## 2026-11-11 GitHub 发布补充验证

- 仓库由所有者预先创建，采用授权 GitHub 写入接口发布到 `main`；首个完整源码提交：`5c31c6cdd7df56c2017a9e8dc05f365794af8e13`。
- 完整源码树：`cd18142e11cfcb50a4f68cb059da68719ff0e57c`，与本地 v0.1.0 Git tree SHA 完全一致（78 个文件，其中 2 张 PNG 截图）。
- 首次 GitHub Actions 运行：https://github.com/magicy565-web/trade-pricing-engine/actions/runs/42962695257 ，状态 `failure`。4 个 job 的 `steps` 都为空，日志未能通过 API 取得；无法据此定位根因，也不宣称 GitHub CI 通过。
- 仓库为 `public`；尚无开源许可证，不要自动宣称为开源项目。

