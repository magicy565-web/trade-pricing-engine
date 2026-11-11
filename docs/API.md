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

### MCP 工具（11 个）

`create_pricing_case`, `list_pricing_cases`, `get_pricing_case`, `list_cost_evidence`, `calculate_quote`, `compare_scenarios`, `submit_quote_approval`, `get_quote`, `replay_quote`, `get_customer_quote`, `evaluate_cashflow`。

不提供确认成本、修改政策、批准、内部定稿、邮件发送或主机文件操作。`get_customer_quote` 返回的是可用于下一步制作/发送的结构化内容；发送授权仍须由外部渠道系统处理。

### 证据能力边界

`reference` 仅为来源标识，不会被服务端自动抓取；`sha256` 是企业提交的原文件摘要，不意味着本引擎验证了文件真实性或交易真实性。成本管理员须实际检查原始资料。报告“confirmed_by”记录谁承担了确认动作，不将人工声明伪装为第三方认证。
