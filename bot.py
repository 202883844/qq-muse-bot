"""网关接收进程：登录、收消息、入队、/ping 直答、脚本级即时回复、botpy 补丁。

按 HANDOVER.md v2 第 4/8/11 节重建。只用 WebSocket 网关，不用 Webhook。
用法:
  .venv/bin/python bot.py            前台运行
  .venv/bin/python bot.py --dry-run  无登录自检：只验补丁、配置是否存在、代理是否设置
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone

import qqbot_common as qc
import quota_guard

# ---------------- 脚本级即时回复（HANDOVER 第 8 节） ----------------
_GREETINGS = {"你好", "您好", "hi", "hello", "在吗", "在么"}
_IDENTITY_KEYS = ("你是谁", "你的名字", "who are you")
_IMAGE_KEYS = ("发张图", "发一张图", "来张图")


def _fast_answer(content):
    """返回即时回复文本则不入队；返回 None 表示入队等 Worker 处理。"""
    text = (content or "").strip()
    low = text.lower()
    if low in _GREETINGS:
        return "你好，我在。有什么事直接说。"
    if any(k in low for k in _IDENTITY_KEYS):
        return "我是接入 Muse 的 QQ 机器人，消息会转给 Muse 处理。"
    # 图片请求不在脚本层处理：需要 Worker 生成图片后走 send_image.py 入队流程
    if any(k in low for k in _IMAGE_KEYS):
        return None
    return None


# ---------------- botpy 1.2.1 适配补丁（HANDOVER 第 11 节，勿删） ----------------
_PATCHES_APPLIED = []


def apply_botpy_patches():
    """对固定版本 qq-botpy==1.2.1 打四个补丁。返回已应用补丁名列表。"""
    global _PATCHES_APPLIED
    if _PATCHES_APPLIED:
        return list(_PATCHES_APPLIED)
    import aiohttp
    from aiohttp import ClientSession, TCPConnector, WSMsgType
    from ssl import SSLContext

    # 补丁 1：给 ConnectionState 补 parse_group_message_create 解析器
    from botpy.connection import ConnectionState
    from botpy.message import GroupMessage

    def parse_group_message_create(self, payload):
        _message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
        self._dispatch("group_message_create", _message)

    ConnectionState.parse_group_message_create = parse_group_message_create
    _PATCHES_APPLIED.append("parse_group_message_create")

    # 补丁 2：替换 BotWebSocket.ws_connect，显式传 proxy 并用 trust_env 会话
    from botpy.gateway import BotWebSocket

    async def ws_connect(self):
        ws_url = self._session["url"]
        if not ws_url:
            raise Exception("[botpy] 会话url为空")
        async with ClientSession(connector=TCPConnector(limit=10, ssl=SSLContext()), trust_env=True) as session:
            async with session.ws_connect(ws_url, proxy=qc.get_proxy()) as ws_conn:
                while True:
                    msg = await ws_conn.receive()
                    if msg.type == WSMsgType.TEXT:
                        await self.on_message(ws_conn, msg.data)
                    elif msg.type == WSMsgType.ERROR:
                        await self.on_error(ws_conn.exception())
                        await ws_conn.close()
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.CLOSE):
                        await self.on_closed(ws_conn.close_code, msg.extra)
                    if ws_conn.closed:
                        break

    BotWebSocket.ws_connect = ws_connect
    _PATCHES_APPLIED.append("ws_connect_proxy_trust_env")

    # 补丁 3：替换 BotHttp.check_session，建会话时带 trust_env=True
    from botpy.http import BotHttp

    async def check_session(self):
        await self._token.check_token()
        self._headers = {
            "Authorization": self._token.get_string(),
            "X-Union-Appid": self._token.app_id,
        }
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=TCPConnector(limit=500, ssl=SSLContext(), force_close=True),
                trust_env=True,
            )

    BotHttp.check_session = check_session
    _PATCHES_APPLIED.append("check_session_trust_env")

    # 补丁 4：替换 Token.update_access_token，取 Token 用 trust_env 会话
    from botpy.robot import Token

    async def update_access_token(self):
        session = aiohttp.ClientSession(trust_env=True)
        data = None
        try:
            async with session.post(
                url="https://bots.qq.com/app/getAppAccessToken",
                timeout=aiohttp.ClientTimeout(total=20),
                json={"appId": self.app_id, "clientSecret": self.secret},
            ) as response:
                data = await response.json()
        finally:
            await session.close()
        if not data or "access_token" not in data or "expires_in" not in data:
            raise RuntimeError(str(data))
        self.access_token = data["access_token"]
        self.expires_in = int(data["expires_in"]) + int(time.time())

    Token.update_access_token = update_access_token
    _PATCHES_APPLIED.append("token_update_trust_env")
    return list(_PATCHES_APPLIED)


# ---------------- 消息规范化与入队 ----------------
def _extract(message, msg_type):
    data = {}
    get = lambda name, default=None: getattr(message, name, default)
    content = get("content") or ""
    attachments = []
    for att in get("attachments") or []:
        if isinstance(att, dict):
            attachments.append(att)
        else:
            attachments.append({
                "content_type": getattr(att, "content_type", None),
                "filename": getattr(att, "filename", None),
                "size": getattr(att, "size", None),
                "url": getattr(att, "url", None),
            })
    author = get("author")
    author_id = None
    if author is not None:
        author_id = getattr(author, "id", None) or getattr(author, "member_openid", None) \
            or getattr(author, "user_openid", None) or (author.get("id") if isinstance(author, dict) else None)
    ts_raw = get("timestamp")
    ts_epoch = 0.0
    if isinstance(ts_raw, (int, float)):
        ts_epoch = float(ts_raw)
    elif isinstance(ts_raw, str) and ts_raw:
        try:
            ts_epoch = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts_epoch = 0.0
    entry = {
        "type": msg_type,
        "msg_id": get("id"),
        "event_id": get("event_id"),
        "content": content,
        "author_id": author_id,
        "group_id": get("group_openid") if msg_type == "group" else None,
        "channel_id": get("channel_id") if msg_type == "channel" else None,
        "guild_id": get("guild_id") if msg_type in ("channel", "dm") else None,
        "timestamp": ts_raw if isinstance(ts_raw, str) else (
            datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat() if ts_epoch else None),
        "timestamp_epoch": ts_epoch,
        "attachments": attachments,
        "media_files": [],
    }
    return entry


async def _direct_reply(entry, text):
    try:
        ok, resp = await qc.send_text_passive(entry, text)
        if not ok:
            print(f"[bot] direct reply failed: {resp}", file=sys.stderr)
    except Exception as e:
        print(f"[bot] direct reply error: {e}", file=sys.stderr)


async def _handle(message, msg_type):
    entry = _extract(message, msg_type)
    content = (entry["content"] or "").strip()
    # /ping 直答，不入队
    if content == "/ping":
        await _direct_reply(entry, "pong")
        return
    # 限额已满：不入队，只回复一条固定暂停通知
    if quota_guard.is_over_quota():
        await _direct_reply(entry, quota_guard.PAUSE_NOTICE)
        return
    # BOT_MODE 不是 muse 时不入队
    if qc.bot_mode() != "muse":
        print(f"[bot] BOT_MODE={qc.bot_mode()!r} != 'muse', skip enqueue", file=sys.stderr)
        return
    # 脚本级即时回复
    fast = _fast_answer(content)
    if fast is not None:
        await _direct_reply(entry, fast)
        return
    # 下载图片附件后入队
    if entry["attachments"]:
        entry["media_files"] = await qc.download_media(entry["attachments"])
    path = qc.enqueue(entry)
    print(f"[bot] enqueued {entry['type']} -> {path.name}")


def build_client():
    import botpy
    from botpy.message import C2CMessage, GroupMessage, Message, DirectMessage  # noqa: F401

    intents = botpy.Intents(public_messages=True, public_guild_messages=True, direct_message=True)
    # 不要加 guild_messages：私域专属，公域请求会被网关以 4014 断开（HANDOVER 第 8 节）

    class MuseQQClient(botpy.Client):
        async def on_ready(self):
            print(f"[bot] ready: {self.robot.name if self.robot else ''}")

        async def on_c2c_message_create(self, message):
            await _handle(message, "c2c")

        async def on_group_at_message_create(self, message):
            await _handle(message, "group")

        async def on_group_message_create(self, message):
            await _handle(message, "group")

        async def on_at_message_create(self, message):
            await _handle(message, "channel")

        async def on_message_create(self, message):
            await _handle(message, "channel")

        async def on_direct_message_create(self, message):
            await _handle(message, "dm")

    return MuseQQClient(intents=intents)


def dry_run():
    patches = apply_botpy_patches()
    # 验证补丁 1 已注册到解析器表
    from botpy.connection import ConnectionState
    probe = ConnectionState.__new__(ConnectionState)
    has_parser = hasattr(probe, "parse_group_message_create")
    result = {
        "dry_run": True,
        "patches_applied": patches,
        "patches_count": len(patches),
        "group_message_parser_registered": has_parser,
        "config_present": qc.config_present(),
        "bot_mode": qc.bot_mode() or None,
        "proxy_configured": qc.proxy_configured(),
        "login_attempted": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ok = len(patches) == 4 and has_parser
    if not result["config_present"]:
        print("warning: .env missing QQ_BOT_APP_ID / QQ_BOT_APP_SECRET (dry-run does not log in)", file=sys.stderr)
    if not result["proxy_configured"]:
        print("warning: no proxy env configured", file=sys.stderr)
    return 0 if ok else 1


def main():
    if "--dry-run" in sys.argv:
        return dry_run()
    apply_botpy_patches()
    if not qc.config_present():
        print("error: .env missing QQ_BOT_APP_ID / QQ_BOT_APP_SECRET", file=sys.stderr)
        return 2
    qc.ensure_queue_dirs()
    client = build_client()
    client.run(appid=qc.app_id(), secret=qc.app_secret())
    return 0


if __name__ == "__main__":
    sys.exit(main())
