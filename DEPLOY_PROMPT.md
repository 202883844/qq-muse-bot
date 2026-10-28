# 在另一个账号部署 qqbot 的提示词（直接整段粘贴给新账号的 Muse）

---

部署这个 QQ 机器人项目：https://github.com/chuzzhang/muse-qqbot/blob/main/HANDOVER.md

部署完成后，机器人必须开箱就能发文字、图片、视频。媒体发送是硬性验收项，不是可选功能。

先说清三个事实，免得你走弯路：
1. 这个仓库里只有交接文档，没有任何代码。不要找备份、不要等代码，直接按 HANDOVER.md（v2）的规格把全部代码重建出来：bot.py、qqbot_common.py、common.py（垫片）、send_reply.py、send_image.py、send_video.py、send_media.py、send_proactive.py、quota_guard.py、ensure_service.py、templates/qqbot-ws.service.tmpl、requirements.txt、.env.example、.gitignore、RESTORE.md，队列目录 queue/{pending,processing,done,failed,media}、generated/、backups/。项目放在 ~/workspace/qqbot-ws/。
2. 依赖版本钉死：qq-botpy==1.2.1、aiohttp>=3.9,<4、python-dotenv>=1.0,<2、PyYAML>=6.0。先建 .venv 装好依赖，再读一遍 botpy 1.2.1 的真实源码（connection.py / gateway.py / http.py / robot.py），照真实接口写补丁，不许凭文档猜。
3. botpy 的四个补丁一个都不能少（HANDOVER 第 11 节）：①给 ConnectionState 补 parse_group_message_create 解析器；②BotWebSocket.ws_connect 显式传 proxy 并用 trust_env=True 的会话；③BotHttp.check_session 建会话带 trust_env=True；④Token.update_access_token 用 trust_env=True 的会话。本机无直连出口、流量必须走已有的代理环境变量；少任何一个补丁都会连不上或取 Token 报 WRONG_VERSION_NUMBER。Intents 只开 public_messages + public_guild_messages + direct_message，绝不要加 guild_messages（会报 4014 被断开）。

媒体发送实现（图片+视频，第一版就必须做完，不许只做图片）：
QQ 私聊/群的富媒体统一走两步：先 POST /v2/users/<author_id>/files（私聊）或 /v2/groups/<group_id>/files（群），body 为 {"file_type": 类型, "file_data": "<文件字节的 base64>", "srv_send_msg": false}，上传超时给足 120 秒，拿到 file_info；再向消息接口发 {"msg_type": 7, "media": {"file_info": "..."}, "content": "<配文>", "msg_id": "<原消息ID>", "msg_seq": <每次随机>}。file_type 映射：1=图片、2=视频、3=语音、4=文件。
- qqbot_common.py 必须提供两个公共函数供所有发送脚本共用：upload_rich_media(entry, data, file_type) 负责第一步并校验返回里有 file_info；send_media_passive(entry, data, kind, caption) 负责两步合一（kind ∈ image/video/audio/file）。不要在每个脚本里各写一份上传逻辑。
- send_image.py（HANDOVER 7.1）：用法 send_image.py <队列文件> <本地图片或 http(s) 地址> "配文"。私聊/群用上面两步（file_type=1）；频道/私信用 multipart form-data（字段 file_image）直发。校验：仅 PNG/JPEG/GIF/WEBP，按文件头魔数判断（不看扩展名），≤10MB。
- send_video.py：用法与 send_image.py 相同，send_video.py <队列文件> <本地视频或 http(s) 地址> "配文"。仅私聊/群走两步（file_type=2）；频道/私信调用时明确报错退出（那条路径不支持视频）。校验：MP4/MOV 看字节 4–8 是否为 ftyp、WebM 看开头 1A45DFA3，≤20MB（base64 单次上传；更大的文件需要分片上传，不在本期范围，遇到就如实告诉用户）。
- send_media.py：通用入口，不带 --kind 时按魔数自动分发到图片或视频逻辑，带 --kind image|video 时按指定类型发送。Worker 平时用它最省事。
- 三个脚本的退出码与 send_reply.py 一致：0=成功（输出 {"sent": true, ...}）、1=发送失败、2=参数缺失（仅文字脚本）、3=限额已满；成功自动把队列文件归档到 queue/done/，失败归档到 queue/failed/，带 --no-archive 时不归档（同一条消息连发两条时第一条加）。
- 图片/视频内容从哪来：Worker 用自己可用的生成能力（绘图/视频生成）把文件准备到 generated/ 下，再调脚本发送。项目代码只负责发送，不负责生成。

凭据处理（这里最容易翻车，严格执行）：
- 我会用四行格式发你 AppID 和 AppSecret，或直接上传 .env 文件。密钥是 l/I、0/O 极易看错的字符串，绝不许靠肉眼转抄：能拿文件原件就用文件；只能从聊天文本取时，逐字符核对长度和内容（AppSecret 应为 32 位），落盘后先单独调 https://bots.qq.com/app/getAppAccessToken 验一次，通了才许装服务。
- 返回 100016 invalid appid or secret 就停下来告诉我，不要猜着改任何字符。常见原因是 AppID 与 AppSecret 不配套或密钥已被重置，让我回 QQ 开放平台成对重新复制或重置。
- 凭据只许写进项目 .env（权限 600，已被 .gitignore 排除），不许出现在聊天回复、日志、代码、备份和 systemd 单元里。BOT_MODE 必须等于 muse。

部署顺序（每步验过再下一步）：
1. python3 -m venv .venv 并装依赖；
2. 写好 .env（chmod 600），跑 .venv/bin/python bot.py --dry-run：四项补丁必须全部应用、群全量解析器已注册、代理已配置，且不真正登录；
3. 单独验 Token 能拿到；
4. .venv/bin/python ensure_service.py --install 再 systemctl enable --now qqbot-ws（ensure_service 负责把当前代理变量渲染进单元；渲染后的单元文件含代理口令，按敏感文件对待，权限 600，不要引用它的内容）；
5. 确认 systemctl is-active 为 active、is-enabled 为 enabled，日志出现登录成功（机器人启动成功）和心跳启动。

再建三个项目外通道（HANDOVER 第 12 节）：
- 主通道钩子 qqbot-muse-reply：每 5 秒轮询 queue/pending/，有消息才唤醒 Worker 回复；脚本带防抢逻辑：processing/ 有停留不到 10 分钟的文件时静默，唤醒后约 90 秒冷却；脚本放在 ~/hooks/scripts/qqbot-muse-reply.sh，先 dry_run 验过（空队列应 silent、有消息应 wake）再 enable。
- 兜底定时任务 qqbot-reply-cron：每 1 分钟扫一次队列，规则与主通道相同。
- 看门狗钩子 qqbot-watchdog：每 60 秒检查单元文件缺失或服务非 active，异常时唤醒执行 ensure_service.py --install 并重新 enable --now 自愈。

Worker 回复规范（写进钩子/cron 的任务说明里）：
- 队列处理：同时看 pending 和 processing；认领=mv 到 processing；processing 超 10 分钟的孤儿先回收，同类从旧到新，group/channel/dm 被动窗口约 5 分钟优先于 c2c 的约 60 分钟；每条处理完确认文件已归档。
- 文字：用 send_reply.py 回复。
- 媒体（重点）：用户要图片、视频、图、照片、动画时，必须真的生成并发送媒体，这条消息才算处理完——只回一条文字不算完成。流程：生成文件到 generated/ → 图片用 send_image.py、视频用 send_video.py、拿不准类型用 send_media.py → 看输出 {"sent": true} 才算成功。生成或发送失败先重试一次；仍失败才许用 send_reply.py 发文字向用户说明失败原因，并在执行摘要里如实报告，不许把文字回复冒充媒体发送成功。
- 汇报纪律：没错误就不汇报，只报真实失败、积压和需要我决定的事。

部署前还要确认：这台机器经代理的出口 IP 已加入 QQ 开放平台的 IP 白名单（出口 IP 自己查了告诉我，我去核对）。如果这是替换旧部署：同一个机器人不许两台机器同时在线，先停旧的再启新的。

完成标准（缺一不可，全部要有实测证据）：
1. 服务 active+enabled，日志有登录成功和心跳；
2. 我在 QQ 私聊发 /ping，立即收到 pong；
3. 发普通文字消息，约 60 秒内收到回复，队列文件归档到 queue/done/；
4. 图片实测：我在 QQ 发一条要图片的消息，收到的是真图片（发送脚本输出 sent:true、HTTP 200），不是文字说明；
5. 视频实测：我在 QQ 发一条要视频的消息，收到可播放的真视频（同上）。注意每条消息的被动回复条数有限（约 5 条），测试时不要浪费。
全程直接干练地汇报：先做后说，只给结论和证据，没验过的不要说已完成。
