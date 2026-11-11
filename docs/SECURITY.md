# 安全、部署、备份与上线门槛

## 默认配置

CLI 只监听 `127.0.0.1`，不接受无凭据的业务操作。HTTP 请求体上限 1 MiB；检查 Host 与 Origin；不启用通配 CORS；不从请求体接受企业身份或审核者身份。页面默认没有客户记录，也不将 token 写入 localStorage/sessionStorage。

MCP 仅使用 agent 凭据，并在启动时拒绝 admin/approver 凭据；默认关闭环境代理，不跟随重定向，远程 API 必须使用 HTTPS。模型不能通过这一工具表面确认成本或批准报价。

**API 权限不等于宿主进程隔离。** 若 OpenMausBot 与服务管理员共用系统账号，且 Worker 有 Shell/任意文件权限，它可能直接读到其他凭据或数据库。生产必须用不同的非特权系统账号/容器挂载：Worker 只拿到 `agent.token` 的单独副本和适配器，不得读取 server.json、其他 token、SQLite、备份或服务运维目录。当前未对你的 Fleet 完成此隔离验收。

这里的审批身份由独立 Bearer 凭据实现，不是企业 SSO、MFA 或不可伪造的人类在场证明。不得将审批凭据交给自动化 Agent，再声称已实现人工审批。

## 数据保护边界

SQLite 与备份在 v0.1 中**不做应用层加密**；Linux 新建私有目录/凭据采用 0700/0600，Windows 需企业管理员额外配置 NTFS ACL。建议配合系统磁盘加密。一个部署服务只绑定一个企业；SQL 中仍执行企业范围隔离，但这不等于提供托管多租户平台。

审计链能够发现无相应重算的记录改动，但拥有数据库和源代码控制权的人能重写整个链。它不是外部公证或防管理员篡改系统。

HTML 文档只输出允许字段并进行转义。只有已批准且仍有效的版本能生成对客文件；成本、政策底价、利润、内部证据均不输出。原始内部 JSON 不应发给买家。

## 部署

先在本机完成真实企业资料验收，再把程序放在企业自己的 Linux/Windows 主机。不要直接把开发模式端口暴露到互联网。远程访问需要企业现有反向代理的 TLS、访问限制、请求体大小/超时/速率控制；`server.json.origin` 必须设为准确 HTTPS Origin，Host 要与之匹配。

Linux systemd 示例（实际路径/系统账号由管理员配置）：

```ini
[Unit]
Description=Trade Pricing Engine
After=network.target

[Service]
User=tradepricing
WorkingDirectory=/opt/trade-pricing-engine
ExecStart=/opt/trade-pricing-engine/.venv/bin/python -m trade_pricing serve --config /var/lib/trade-pricing/server.json
Restart=on-failure
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/trade-pricing

[Install]
WantedBy=multi-user.target
```

此为部署配置示例，本次没有安装你的生产服务、修改腾讯云或公开发布。

## 凭据撤销/轮换

在维护窗口编辑受保护的 `server.json`，移除旧 principal 或替换其 token_hash，然后重启服务。原 raw token 应安全删除，分发新凭据时不要使用聊天正文或 GitHub。当前没有在线 SSO/凭据管理后台。

## 备份与恢复

```sh
python scripts/backup.py --config private/server.json --out /protected-backups/pricing-YYYYMMDD.sqlite
```

脚本使用 SQLite backup API 生成一致性副本，拒绝覆盖已有目标，执行 `integrity_check` 并打印摘要。不要直接只复制正在运行的 `.sqlite` 而忽略 WAL。备份包含企业私密经营信息，不可提交到 Git。

恢复必须停止服务，保留当前数据库与 WAL 文件，再从核验过的备份恢复到**新路径**，修改受保护配置指向新路径后启动。不要运行时用旧数据库覆盖新业务数据。验证版本、订单记录、审计、原始文件来源和权限后才恢复操作。

## 正式上线前还需完成

真实企业财务复算与业务审核；Windows/最终主机验收；真实 OpenMausBot 工具协商；独立系统账号与跨进程隔离；TLS/防火墙/访问控制；备份恢复演练；依赖漏洞与第三方独立安全评估。当前没有真实市场价格验证、外发测试、商业成交或安全认证。
