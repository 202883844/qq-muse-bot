# 在另一台机器 / 另一个账号部署本项目（给部署方 AI 的说明）

本仓库是完整可运行的代码，不是文档仓库。部署 = 克隆 → 装依赖 → 填凭据 → 验 Token → 装服务 → 建消息处理通道 → 实测。不要去任何其他仓库找代码，不要重写任何文件。

仓库地址：https://github.com/202883844/qq-muse-bot

## 平台侧前置（先在 QQ 开放平台做完，漏做必出故障）

新建的机器人应用默认处于沙箱模式：未正式发布前，只有沙箱配置里登记过的成员和群能与机器人交互。部署前让所有者在开放平台该机器人的管理页完成：

1. 「开发管理 → 沙箱配置 → 在消息列表配置」：点「添加成员」，加入所有需要私聊机器人的 QQ 号（先加所有者本人）。被加入的成员还要用手机 QQ 扫机器人的二维码、把机器人加为好友，私聊才真正开通。**漏了这一步的典型症状就是：群里能回复、私信永远不回复——消息根本到不了机器人，不是代码问题。**
2. 「沙箱配置 → 在 QQ 群配置」：加入用于测试的群（群主在手机 QQ 里把机器人添加到群聊也可）。
3. 本机出口 IP 加入该应用的 IP 白名单（见部署步骤 7）。
4. 若要让沙箱名单外的人也能私聊/拉群使用，必须在平台提交「发布」并通过审核；发布前不要拿名单外的 QQ 号验收私聊，会误判成部署失败。

## 部署步骤

1. 克隆到部署目录（目录名建议 qqbot-ws，与服务名一致）：
   `git clone https://github.com/202883844/qq-muse-bot.git ~/workspace/qqbot-ws`
2. `cd ~/workspace/qqbot-ws && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
   依赖已钉死（qq-botpy==1.2.1 等）。botpy 的四个补丁已经写在 bot.py 里（启动时自动应用），Intents 也已按可用组合写死，不要改动、不要加 guild_messages（会报 4014）。
3. 凭据：向项目所有者索取 QQ 开放平台的 AppID 与 AppSecret。
   - 绝不靠肉眼转抄：能拿文件原件就用文件；只能从聊天文本取时逐字符核对长度（AppSecret 应为 32 位）。
   - 复制 .env.example 为 .env，填入 QQ_BOT_APP_ID、QQ_BOT_APP_SECRET、BOT_MODE=muse，chmod 600。凭据只许进 .env，不许出现在聊天、日志、代码和备份里。
   - 先单独验 Token：调 https://bots.qq.com/app/getAppAccessToken 能拿到 access_token 才继续。返回 100016 invalid appid or secret 就停下来找所有者成对重新复制或重置密钥，不许猜字符改。
4. `.venv/bin/python bot.py --dry-run`：应显示四项补丁已应用、群消息解析器已注册、代理已配置。
5. `.venv/bin/python ensure_service.py --install` 后 `systemctl enable --now qqbot-ws`。
   确认 `systemctl is-active qqbot-ws` = active、is-enabled = enabled，日志出现机器人启动成功与心跳启动。本机流量走代理环境变量，ensure_service 会把代理渲染进服务单元（渲染后的单元按敏感文件对待）。
6. 建消息处理通道（让排队消息有人回复）：等效实现以下三件，间隔照抄：
   - 主通道：每 5 秒轮询 queue/pending/，有消息才唤醒处理；
   - 兜底通道：每 1 分钟扫一次队列；
   - 看门狗：每 60 秒检查服务，单元缺失或服务非 active 时执行 ensure_service.py --install 并 enable --now 自愈。
7. 部署前确认本机经代理的出口 IP 已加入 QQ 开放平台 IP 白名单（查好出口 IP 报给所有者核对）。

## 消息处理规则（Worker 规范）

- 队列：同时看 queue/pending/ 与 queue/processing/；认领 = mv 到 processing/；processing/ 停留超 10 分钟的孤儿先回收；同类从旧到新；group/channel/dm 被动窗口约 5 分钟，优先于 c2c 的约 60 分钟；每条处理完确认已归档到 done/ 或 failed/。
- 文字回复：`.venv/bin/python send_reply.py queue/processing/<文件> "回复文本"`。
- 媒体请求必须真发媒体，只回文字不算完成：要图片 → 用自身生成能力把图片生成到 generated/，再 `.venv/bin/python send_image.py queue/processing/<文件> <图片> "配文"`；要视频 → 同理用 send_video.py（仅 c2c/group，MP4/MOV/WebM，≤20MB）；或用 send_media.py 自动分发。输出 {"sent": true} 才算成功；失败先重试一次，仍失败才用 send_reply.py 发文字说明原因并如实汇报。同一条消息连发两条时第一条加 --no-archive。
- 汇报纪律：无错误不汇报，只报真实失败、明显积压和需要所有者决定的事。

## 故障排查

- 群能回复、私信不回复：先查平台侧沙箱配置——发私信的 QQ 号是否在「沙箱配置 → 在消息列表配置」的成员里、是否已扫码加机器人为好友；机器人未发布时名单外私聊一律无响应。代码侧可用队列验证：私信根本不产生 queue/pending 文件 = 消息没到机器人（平台侧问题）；有文件但没发出 = 再查发送链路。
- 任何消息都不回复：按服务状态 → 队列积压 → Token 校验 → IP 白名单的顺序查，不要反复重装。

## 重要限制

- 同一个机器人不许两台机器同时在线（会互相踢下线）。若是替换旧部署，先停旧机器的 qqbot-ws 服务再启新的。
- 每条用户消息的被动回复条数有限（约 5 条），测试时别浪费。

## 完成标准（全部要有实测证据）

1. 服务 active + enabled，日志有登录成功与心跳；
2. 所有者在 QQ 私聊发 /ping，立即收到 pong（验收用的 QQ 号必须已在沙箱「消息列表配置」里且已加机器人为好友，未发布的机器人对名单外私聊必然无响应，不要据此判定部署失败）；
3. 发普通文字消息，约 60 秒内收到回复，队列文件归档到 queue/done/；
4. 发一条要图片的消息，收到真图片（脚本输出 sent:true、HTTP 200），不是文字说明；
5. 发一条要视频的消息，收到可播放的真视频（同上）。

全程先做后汇报，只给结论和证据，没验过的不要说已完成。
