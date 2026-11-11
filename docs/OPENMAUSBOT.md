# OpenMausBot 接入：复用宿主，不创建新 Runtime

1. 在报价服务所属的隔离账号中启动 HTTP 服务，确认 `/health` 正常。财务人员分别保管 admin/approver 凭据。
2. 将本项目安装到 Worker 可使用的 Python 环境。仅把 `agent.token` 单独提供给 Worker 账号；不能让它读取整个私有目录或 SQLite。
3. 在 OpenMausBot 的实际 MCP 设置中注册 stdio 进程：`command` 为该环境的 Python 绝对路径；`args` 为 `-m trade_pricing mcp --url http://127.0.0.1:8875 --token-file <agent专用密钥路径>`。使用企业实际的端口/HTTPS 地址。
4. 加载 `skills/trade-pricing/SKILL.md`。由宿主已连接的邮箱/聊天工具读取真实询盘，用产品资料库的真实产品 ID/修订创建案件。
5. 首次仅执行 list 工具验证作用域，再用企业批准的内部测试案件跑通计算和“提交审核”。审核由独立财务入口处理，Worker 不取得审批 token。

本次交付未改写你的 OpenMausBot Bot 配置、岗位指令、现有 business-loop、企业资料库或任何生产数据。不能据本仓库的本地 MCP 联调就声明你的 OpenMausBot 已连接。

## 版本

实现并实测的是 MCP 2025-11-25 stdio 工具子集：JSON-RPC 初始化、initialized 通知、ping、tools/list、tools/call。支持与更早的 2025-06-18、2025-03-26、2024-11-05 协商。未知协议版本时返回受支持版本，由客户端决定是否接受。它不假称实现 2026-07-28 新协议。

这是原生 stdio 适配器；不要把 `/v1` REST 根路径配置成远程 MCP Endpoint。远程 Streamable HTTP、OAuth 与 ChatGPT 连接器发布属于后续单独的安全工作。

## 与既有供应商报价系统的区别

既有 buyermcp 的报价属于“供应商向买手给出的采购成本”。本系统输出“出口企业向海外买家给出的销售报价”。不直接复用同一个金额字段。迁移时应保留供应商报价修订、币种、数量范围、有效期、单位和原始文件，补足 named_place 与成本覆盖范围，再由成本管理员确认 CostBook。历史 float 金额不要未经原始文件复核就转换为正式成本。

## 最小宿主验收

验证 15 个工具确实出现；agent 只能提交审核，不能确认成本/批准；成本不足得到明确阻断；真实案件变更后旧报价无法继续；批准后对客字段不泄露成本；断开重连后同一 request_id 不新增报价。完成这些步骤并保存宿主真实工具回执后，才能标记“OpenMausBot 已接通”。



v0.2 增加 revise_pricing_case、promote_pricing_scenario、get_cost_evidence、get_current_pricing_policy。选定比较方案时，优先调用 promote_pricing_scenario：一次操作更新原案件并生成新草稿；不要创建重复案件，也不要把旧审批附到新报价。所有变更保留相同 request_id 重试；版本冲突后先重新读取并确认新输入。

list_cost_evidence 可按 kind 和 active_only 筛选，list_pricing_cases 和 list_cost_evidence 都需要跟随 next_cursor。审批和撤回仍由独立人员使用网页或受认证 HTTP 完成。真实 OpenMausBot 宿主尚未验收；本轮验证的是独立 MCP 子进程到本地 HTTP 的实际调用。
