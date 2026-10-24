# Web 间歇 HTTP 403：请求组与 RPC 重放身份冲突

2026-10-24，本机已对用户原始失败完成历史关联和旧／新真实程序隔离对照。原 support ID 为 `cmd_15ffa5ca_1c64_421b_8176_4bc9833ecf33`，原 Web 隧道为 `tunnel_6ab88858a46c8191b166fb6899205d94`。

## 根因及修复

旧 Bridge 把提供方 `x-request-id` 请求组编号直接作为 capability 的单次重放身份。提供方在一个请求组内可发送多个不同 JSON-RPC 调用；旧实现将后续合法调用误判成重放，返回 HTTP 403 / `request id replay detected`。提供方收到 `id:null` 的 JSON-RPC 错误时进一步包装为 `target_http / invalid_mcp_error`。

已安装修复以带类型的 `[provider_request_id, "rpc", jsonrpc_id]` 的 SHA256 构造 `rpc-v2:` 身份；无 ID 通知使用单独通知域和正文摘要。组内不同 RPC 可正常执行；相同 RPC 的重复仍被持久化重放保护拒绝，重启后也不能重复执行。旧 bare-ID 消费记录仍保守拒绝，不删除授权、不轮换身份或密钥。

## 原始历史证据

2026-10-22 15:20:43 +08，原隧道记录转发 RPC `dbdacf56-267f-413e-b767-8b45be70aefc`；15:20:49 和 15:20:59 的不同 RPC 均返回 403。最后一个就是原 support ID。三者共用请求组 `d3697cba-634c-4fd9-823c-1f57a995b54a`。修复前 authority 快照包含该组的已消费 bare ID，故障时 capability 租约有效。历史日志未保存拒绝正文；具体拒绝分支由下述二进制对照重现验证，不能声称历史日志直接保存了该正文。

旧 `bridge.restart.log` 的 grant 不匹配记录最后更新于 2026-09-19 UTC，属于旧启动故障，不是本次已运行进程的请求组冲突。

## 可重复的真实二进制对照

使用实际原 g34 二进制和实际已安装修复二进制，在隔离 HOME、测试专用 authority/key 和私有 UDS 中执行原 SPEC 路径和原请求组／RPC 编号。测试不读取生产密钥、不修改生产状态、不重启生产进程。

| 条件 | 原 g34 | 已安装修复 |
| --- | --- | --- |
| 组内第一 RPC | 200 | 200 |
| 同组不同 RPC | 403 / replay detected | 200 |
| 原 RPC 真正重复 | 403 | 403 |
| 测试进程重启后重复原 RPC | 403 | 403 |
| 重启后同组新 RPC | 403 | 200 |

FileRead 由 Bridge 直接处理，所以测试备用 backend 调用数为零；这不是远端 backend 转发验收。额外 ingress 回归覆盖同 RPC 改参数、JSON ID 类型、通知域、历史 bare-ID 等安全边界。

执行入口：

```sh
python3 scripts/reproduce_request_group_replay.py \
  --old-binary /path/to/original/local-tool-bridge \
  --fixed-binary /path/to/fixed/local-tool-bridge \
  --read-path /path/to/harmless/SPEC.md \
  --output /path/to/new-evidence.json
```

本机原件在 `tmp/web-controller-repair-20261023/`：`original403-historical-causal-evidence.json`、`original403-old-vs-installed-binary-ab.json`、`original403-portable-ab-regression.json`。私有证据和生产 authority/key 不纳入 Git。真实 Web 总控的身份恢复、结果读回及生产控制事件另有独立验收；二进制 A/B 与浏览器验收各自证明对应边界。
