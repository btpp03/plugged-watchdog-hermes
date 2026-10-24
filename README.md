# plugged-watchdog-hermes

Keep a **plugged.host**（noprofit.space 系）免费容器服务器处于运行状态：每次运行打开面板
Console，读状态；如果是 `Offline` 就按 `Start`，已经是 `Running/Starting` 就什么都不做。

- 只做「开/停」这一件事，不动容器里的文件，不装任何东西。
- 机器上跑的是**哪吒 agent**（纯监控探针），不是代理；plugged 的进程名扫描只封 sing-box 这类代理内核。
- 服务器被平台 `suspend`（滥用扫描）时脚本只报告，不尝试绕过。

## 凭什么能在云端跑

面板登录用 cookie（`NOPROFIT_COOKIES`）。session 死了会退到用 `PLUGGED_LOGIN` / `PLUGGED_PWD`
重新登录，Turnstile 用 CDP 真鼠标点（widget 在 closed shadow root 里，locator 摸不到）。

## 配置（仓库 Settings → Secrets and variables → Actions）

| 名称 | 类型 | 说明 |
|---|---|---|
| `NOPROFIT_COOKIES` | secret | `/root/plugged_cookies.json` 的内容，原始 JSON 或 base64 都行 |
| `PLUGGED_LOGIN` | secret | 备用账号，例如 `btpp03b` |
| `PLUGGED_PWD` | secret | 备用密码 |
| `PLUGGED_SERVER` | variable（可选） | 服务器 short uuid，默认 `9b071dd5`（budgerigar） |

## 手动跑

```bash
gh workflow run watchdog.yml -R btpp03/plugged-watchdog-hermes
gh run watch -R btpp03/plugged-watchdog-hermes
```

## 退出码

- `0` 在线或已成功 Start
- `1` 认证失败（cookie 过期且备用账号密码登不上）→ 需要换 cookie
- `2` 服务器被 suspend → 面板里能看到扫描原因，脚本不处理
- `3` 点了 Start 但状态没变

## 定时

workflow 自带 `schedule: */15`。GitHub 原生 schedule 偶尔不按时触发，要更准就把
`workflow_dispatch` 挂到 cron-job.org 外部腿上（`POST /repos/btpp03/plugged-watchdog-hermes/actions/workflows/watchdog.yml/dispatches`）。
