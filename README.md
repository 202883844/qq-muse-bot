# QQ Muse Bot

由 Muse 驱动的 QQ AI 机器人：能聊天、能生成并发送图片和视频。
仓库名：`qq-muse-bot`

> GitHub 仓库设置建议 —— About：`由 Muse 驱动的 QQ AI 机器人，能发文字、图片和视频`；Topics：`qq-bot`、`ai-bot`、`chatbot`、`qq`、`muse`

---

QQ 开放平台官方机器人（WebSocket 网关模式，不用 Webhook、不需要域名和证书）。
收消息与回消息解耦：`bot.py` 只负责登录网关、收消息、下载图片附件并写入文件队列；
外部 Worker 认领队列文件、决定回复内容，再调发送脚本以被动回复发出。

## 能力

- 文字被动回复（私聊 / 群 / 频道 / 频道私信）
- 图片发送：私聊/群走富媒体两步（上传 `file_type=1` → `msg_type=7`），频道/私信走 multipart
- 视频发送：私聊/群富媒体两步（`file_type=2`），MP4/MOV/WebM，≤20MB
- 通用媒体入口 `send_media.py`：按文件魔数自动分发图片/视频
- `/ping` 直答、脚本级问候/身份即时回复、每周用量限额保护（`quota_guard.py`）
- systemd 托管（`Restart=always`），另配队列轮询 Worker、兜底定时扫描和看门狗自愈（见 `DEPLOY_PROMPT.md`）

## 结构

| 文件 | 作用 |
| --- | --- |
| `bot.py` | 网关接收进程：登录、收消息、入队、`/ping` 直答、botpy 适配补丁 |
| `qqbot_common.py` | 公共库：`.env` 读取、代理、队列、Token、富媒体上传/发送、附件下载 |
| `send_reply.py` / `send_image.py` / `send_video.py` / `send_media.py` | 文字 / 图片 / 视频 / 通用媒体被动回复，发送后自动归档队列文件 |
| `send_proactive.py` | 主动消息（兜底用，平台默认无群主动消息权限） |
| `quota_guard.py` | 每周用量达到 100% 时暂停入队与发送 |
| `ensure_service.py` | 把 `templates/qqbot-ws.service.tmpl` 渲染成 systemd 单元并安装 |
| `queue/` | 消息队列：`pending` 待处理、`processing` 处理中、`done` 已回复、`failed` 失败、`media` 附件图片 |

## 部署

前置：已在 QQ 开放平台创建机器人并拿到 AppID / AppSecret；本机出口 IP 已加入平台 IP 白名单；
本机有 Python 3 和 systemd；无直连出口的环境必须有可用的代理环境变量。

```bash
cd ~/workspace/qqbot-ws
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填入 QQ_BOT_APP_ID / QQ_BOT_APP_SECRET，确认 BOT_MODE=muse
chmod 600 .env
.venv/bin/python bot.py --dry-run          # 无登录自检：补丁、配置、代理
.venv/bin/python ensure_service.py --install
systemctl enable --now qqbot-ws
```

验证：QQ 私聊机器人发 `/ping` 应立即收到 `pong`；发普通消息应收到回复且队列文件归档到 `queue/done/`；
发要图片/视频的消息应收到真实媒体。

一键交给 AI 部署：把 `DEPLOY_PROMPT.md` 全文发给你的 AI 助手，再单独给它凭据即可。

从备份恢复见 `RESTORE.md`。

## 注意

- 凭据只放 `.env`（权限 600，已被 `.gitignore` 排除），不要提交进仓库、不要写进代码或日志。
- 依赖 `qq-botpy==1.2.1` 的内部适配在 `bot.py` 的 `apply_botpy_patches()` 里，升级 botpy 前逐项复核。
- Intents 只开 `public_messages + public_guild_messages + direct_message`；公域机器人加 `guild_messages` 会被网关以 4014 断开。
- 被动回复有窗口期：私聊约 60 分钟，群/频道约 5 分钟，过期无法补发。
- 同一个机器人不要在两台机器同时运行。
