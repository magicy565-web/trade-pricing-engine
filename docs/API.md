# HTTP / MCP 合同

所有 `/v1/` 请求都必须带 `Authorization: Bearer <role-token>`。调用方不能在请求体指定企业 ID、审核者身份或权限；这些来自服务端凭据映射。只有 `/health`、网页壳和固定静态文件公开。

| 方法/路径 | 用途 | 写入角色 |
|---|---|---|
| GET /v1/me | 当前身份 | 只读 |
| GET /v1/openapi | 与运行代码对应的 OpenAPI | 只读 |
| GET, POST /v1/cases | 列出/建立询盘 | admin / agent |
| GET, PUT /v1/cases/{id} | 读取/替换新修订 | admin / agent |
| GET /v1/snapshots | 已确认快照（含内部成本） | 只读 |
| POST /v1/cost-snapshots | 确认成本 | admin |
| POST /v1/fx-snapshots | 确认显式汇率 | admin |
| POST /v1/policies | 确认新政策并撤销旧政策 | admin |
| POST /v1/snapshots/{id}/revoke | 撤销依据 | admin |
| POST /v1/cases/{id}/calculate | 新建不可变报价内容 | admin / agent |
| POST /v1/cases/{id}/compare | 比较输入的场景，不产生正式报价 | 只读计算 |
| GET /v1/quotes/{id} | 内部完整结果及当前阻断理由 | 只读 |
| GET /v1/quotes/{id}/replay | 按历史时钟重放，核对哈希 | 只读 |
| POST /v1/quotes/{id}/submit | 提交确切版本审核 | admin / agent |
| POST /v1/quotes/{id}/approval | 批准/驳回 | approver |
| POST /v1/quotes/{id}/finalize | 内部定稿，不发送 | approver |
| GET /v1/quotes/{id}/customer | 对客允许字段 | 只读，且内容已批准、仍有效 |
| GET /v1/quotes/{id}/document | 英文 HTML 报价 | 同上 |
| POST /v1/cashflow | 显式现金流日程分析 | 只读计算 |
| GET /v1/audit/verify | 核验审计哈希链 | 只读 |

建立案件的请求是 `{request_id, case}`；导入网页时上传内层 `case` 对象。
成本/汇率/政策请求分别是 `{request_id, cost_book}`、`{request_id, fx_book}`、`{request_id, policy}`；网页上传对应内层对象。运行合同见 `schemas/`，不是手写后与实现脱节的示例。

计算请求：

```json
{
  "request_id": "a-stable-id-for-this-specific-operation",
  "expected_revision": 1,
  "cost_snapshot_id": "cost_actual-id-from-server",
  "fx_snapshot_id": "fx_actual-id-from-server",
  "policy_snapshot_id": "policy_actual-id-from-server",
  "proposed_unit_price": null
}
```

这里的 ID 仅说明格式，不是生产数据。`null` 表示使用政策目标报价；显式议价价格必须是十进制字符串。JSON number、指数记法、NaN、Infinity、过多小数均被拒绝。

提交/定稿要求 `{request_id, expected_version, content_hash}`。审批另外要求 `decision: approve|reject` 与非空 `reason`。客户端显示过的数据不等于审批绑定，服务端必须重新检查当前版本与有效性。

### 错误约定

`{"error":{"code":"...","message":"..."}}`；输入错误返回 422 且不回显整个敏感输入。身份错误 401，越权 403，不属于当前企业/不存在的资源 404，版本冲突/依据撤销/过期/利润越界 409，存储暂不可用 503。

**响应丢失：重试完全相同 request_id 与输入。不要为同一不明结果生成新 request_id。** 幂等凭据包括操作者：不要跨角色把同一次操作当作同一个安全主体。

### MCP 工具（15 个）

`create_pricing_case`, `revise_pricing_case`, `promote_pricing_scenario`, `get_cost_evidence`, `get_current_pricing_policy`, `list_pricing_cases`, `get_pricing_case`, `list_cost_evidence`, `calculate_quote`, `compare_scenarios`, `submit_quote_approval`, `get_quote`, `replay_quote`, `get_customer_quote`, `evaluate_cashflow`。

不提供确认成本、修改政策、批准、内部定稿、邮件发送或主机文件操作。`get_customer_quote` 返回的是可用于下一步制作/发送的结构化内容；发送授权仍须由外部渠道系统处理。

### 证据能力边界

`reference` 仅为来源标识，不会被服务端自动抓取；`sha256` 是企业提交的原文件摘要，不意味着本引擎验证了文件真实性或交易真实性。成本管理员须实际检查原始资料。报告“confirmed_by”记录谁承担了确认动作，不将人工声明伪装为第三方认证。


## v0.2 新增接口

| 方法/路径 | 用途 | 权限 |
|---|---|---|
| POST /v1/cases/{id}/promote-scenario | 重新校验并原子选定方案：更新案件、生成未批准草稿 | admin / agent |
| GET /v1/snapshots/{id} | 按 ID 读取依据及撤销状态，核验存储哈希 | 内部已认证角色 |
| GET /v1/policies/current | 独立读取最近未撤销政策；没有政策时返回 policy=null | 内部已认证角色 |
| POST /v1/quotes/{id}/withdraw | 撤回报价，保留历史决策与重算 | 仅 approver |

列表 `GET /v1/cases` 与 `GET /v1/snapshots` 接受 `limit`（1–100，默认 50）及 `before`（上页的 `next_cursor`）。响应保留 `cases` / `snapshots` 数组并新增 `next_cursor`；为 null 时结束。顺序为稳定的创建顺序倒序，编辑案件不会改变游标位置。客户端必须读取后续页，不能假设首个数组是全部记录。

快照列表另外接受 `kind=cost|fx|policy` 和 `active_only=true|false`。active_only 仅指未撤销，不承诺证据未过期；计算和审批时仍会核验有效期。当前政策接口同样不跳过有效期检查。

选定方案请求为 `{request_id, expected_revision, fx_snapshot_id, policy_snapshot_id, scenario}`；scenario 与比较输入中的单个方案结构相同。响应包含 `{case_id, case_revision, quote, approval_required: true, delivery_status: "NOT_SENT"}`。需要完整重新提交方案输入，不信任客户端计算结果。案件版本改变返回 409；数量或条款格式错误返回 422；证据不适用、利润不足返回 409 且回滚整个事务。选择成功会递增案件版本，使旧报价失效，即使选定方案仅修改价格。

撤回请求为 `{request_id, expected_version, content_hash, reason}`，原因不得全为空白。支持 draft / pending_approval / approved / issued → withdrawn；已过期或依据已撤销的报价仍可撤回。撤回不可恢复，后续重新计算并审批；不提供 Worker 撤回工具。GET quote 的 `decisions` 包含 approve / reject / withdraw 的操作者、原因和哈希。这里的 withdrawn 不表示已通知外部接收人。

同案件修订下直接调用 calculate 会保留多个独立报价。若新价格旨在替代旧价，应使用原案件选定流程使旧修订失效，或由审批员明确撤回旧报价。禁止把“创建新报价”当作“所有旧报价已自动撤销”。

幂等重试返回原操作回执，并不重新认证其今天的可导出状态。消费回执后、真正制作或发送文件前，应重新 GET 当前报价及 customer/document 接口；外部发送仍需独立授权。
