"""公共库：.env 读取、代理、队列读写、Token 获取、download_media()。

重建说明：按 HANDOVER.md（v2, 2026-10-27）第 2/3/5/7/11 节规格重写。
凭据只从项目目录 .env 读取，绝不写日志。
"""
import asyncio
import base64
import json
import os
import random
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import aiohttp
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
QUEUE_DIR = BASE_DIR / "queue"
PENDING_DIR = QUEUE_DIR / "pending"
PROCESSING_DIR = QUEUE_DIR / "processing"
DONE_DIR = QUEUE_DIR / "done"
FAILED_DIR = QUEUE_DIR / "failed"
MEDIA_DIR = QUEUE_DIR / "media"
GENERATED_DIR = BASE_DIR / "generated"

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),  # 再验 WEBP 标识
)

MAX_MEDIA_BYTES = 15 * 1024 * 1024  # 接收上限 15 MB（HANDOVER 7.2）
MAX_SEND_IMAGE_BYTES = 10 * 1024 * 1024  # 发送上限 10 MB（HANDOVER 7.1）

# 被动回复窗口（秒），仅作警告判断，不是硬拒绝（HANDOVER 7.3）
PASSIVE_WINDOWS = {"c2c": 60 * 60, "group": 5 * 60, "channel": 5 * 60, "dm": 5 * 60}

load_dotenv(ENV_PATH)


def get_env(name, default=""):
    return os.environ.get(name, default) or default


def app_id():
    return get_env("QQ_BOT_APP_ID")


def app_secret():
    return get_env("QQ_BOT_APP_SECRET")


def bot_mode():
    return get_env("BOT_MODE")


def config_present():
    return bool(app_id() and app_secret())


def get_proxy():
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        val = os.environ.get(key)
        if val:
            return val
    return None


def proxy_configured():
    return get_proxy() is not None


def make_aiohttp_session(**kwargs):
    """项目自有 REST 请求统一用此会话：默认 trust_env=True（HANDOVER 第 11 节）。"""
    kwargs.setdefault("trust_env", True)
    return aiohttp.ClientSession(**kwargs)


# ---------------- Token ----------------
_token_cache = {"access_token": None, "expires_at": 0.0}


async def get_access_token(session=None):
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    own = session is None
    if own:
        session = make_aiohttp_session()
    try:
        async with session.post(
            TOKEN_URL,
            json={"appId": app_id(), "clientSecret": app_secret()},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            data = await resp.json()
    finally:
        if own:
            await session.close()
    if "access_token" not in data:
        raise RuntimeError(f"getAppAccessToken failed: {data}")
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = now + int(data.get("expires_in", 7200))
    return _token_cache["access_token"]


def auth_headers(token):
    return {"Authorization": f"QQBot {token}", "X-Union-Appid": app_id()}


# ---------------- 队列 ----------------
def ensure_queue_dirs():
    for d in (PENDING_DIR, PROCESSING_DIR, DONE_DIR, FAILED_DIR, MEDIA_DIR, GENERATED_DIR):
        d.mkdir(parents=True, exist_ok=True)


def new_queue_id():
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def enqueue(entry):
    ensure_queue_dirs()
    entry.setdefault("id", new_queue_id())
    entry.setdefault("enqueued_at", time.time())
    path = PENDING_DIR / f"{entry['id']}.json"
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_queue_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def archive_queue_file(path, success, no_archive=False):
    if no_archive:
        return None
    src = Path(path)
    if not src.exists():
        return None
    dest_dir = DONE_DIR if success else FAILED_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    src.replace(dest)
    return dest


def passive_window_expired(entry):
    window = PASSIVE_WINDOWS.get(entry.get("type"))
    if not window:
        return False
    ts = entry.get("timestamp_epoch") or entry.get("enqueued_at") or 0
    if not ts:
        return False
    return (time.time() - float(ts)) > window


def target_path(entry):
    t = entry.get("type")
    if t == "c2c":
        return f"/v2/users/{entry.get('author_id')}/messages"
    if t == "group":
        return f"/v2/groups/{entry.get('group_id')}/messages"
    if t == "channel":
        return f"/channels/{entry.get('channel_id')}/messages"
    if t == "dm":
        return f"/dms/{entry.get('guild_id')}/messages"
    raise ValueError(f"unknown queue type: {t!r}")


def random_msg_seq():
    return random.randint(1, 2**31 - 1)


async def post_passive_message(entry, body):
    """向目标地址发一条被动回复（带原 msg_id），返回 (ok, response_dict)。"""
    token = await get_access_token()
    url = API_BASE + target_path(entry)
    async with make_aiohttp_session() as session:
        async with session.post(url, json=body, headers=auth_headers(token)) as resp:
            try:
                data = await resp.json()
            except Exception:
                data = {"raw": await resp.text()}
            return resp.status < 400, {"status": resp.status, **(data if isinstance(data, dict) else {"data": data})}


async def send_text_passive(entry, content):
    body = {"content": content, "msg_id": entry.get("msg_id")}
    if entry.get("type") in ("c2c", "group"):
        body["msg_type"] = 0
        body["msg_seq"] = random_msg_seq()
    return await post_passive_message(entry, body)


# ---------------- 图片 ----------------
def detect_image_format(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_send_image(data):
    if len(data) > MAX_SEND_IMAGE_BYTES:
        raise ValueError(f"image too large: {len(data)} > {MAX_SEND_IMAGE_BYTES}")
    fmt = detect_image_format(data)
    if not fmt:
        raise ValueError("unsupported image format (need PNG/JPEG/GIF/WEBP magic)")
    return fmt


async def _fetch_bytes(url, timeout=10):
    async with make_aiohttp_session() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            resp.raise_for_status()
            return await resp.read()


async def fetch_image_bytes(source):
    """本地路径直接读；http(s) 地址先直连、失败转 weserv 抓取（HANDOVER 7.1/7.2）。"""
    if not re.match(r"^https?://", source):
        return Path(source).read_bytes()
    try:
        return await _fetch_bytes(source, timeout=10)
    except Exception:
        weserv = "https://images.weserv.nl/?url=" + quote(source, safe="")
        return await _fetch_bytes(weserv, timeout=20)


# ---------------- 富媒体（图片/视频/语音/文件） ----------------
# QQ 官方富媒体 file_type：1=图片 2=视频 3=语音 4=文件
FILE_TYPE_IMAGE = 1
FILE_TYPE_VIDEO = 2
FILE_TYPE_AUDIO = 3
FILE_TYPE_FILE = 4
MEDIA_KIND_TO_FILE_TYPE = {"image": 1, "video": 2, "audio": 3, "file": 4}
MAX_VIDEO_BYTES = 20 * 1024 * 1024  # 视频单次 base64 上传上限（本地保护值，超大文件需分片上传）


def detect_video_format(data):
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "mp4"  # mp4 / mov / m4v 均为 ftyp 盒
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return None


def validate_media(data, kind):
    if kind == "image":
        return validate_send_image(data)
    if kind == "video":
        if len(data) > MAX_VIDEO_BYTES:
            raise ValueError(f"video too large: {len(data)} > {MAX_VIDEO_BYTES}")
        fmt = detect_video_format(data)
        if not fmt:
            raise ValueError("unsupported video format (need MP4/MOV/WebM)")
        return fmt
    if kind in ("audio", "file"):
        if len(data) > MAX_VIDEO_BYTES:
            raise ValueError(f"media too large: {len(data)}")
        return kind
    raise ValueError(f"unknown media kind: {kind}")


async def upload_rich_media(entry, data, file_type):
    """c2c/group 两步发送的第一步：上传到 /v2/{users|groups}/<id>/files 拿 file_info。"""
    t = entry.get("type")
    if t not in ("c2c", "group"):
        raise ValueError(f"rich media upload unsupported for type {t}")
    token = await get_access_token()
    target_id = entry.get("author_id") if t == "c2c" else entry.get("group_id")
    kind = "users" if t == "c2c" else "groups"
    async with make_aiohttp_session() as session:
        async with session.post(
            f"{API_BASE}/v2/{kind}/{target_id}/files",
            json={"file_type": file_type, "file_data": base64.b64encode(data).decode(), "srv_send_msg": False},
            headers=auth_headers(token),
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            data_resp = await resp.json()
            if resp.status >= 400 or "file_info" not in data_resp:
                raise RuntimeError(f"file upload failed: {data_resp}")
            return data_resp["file_info"]


async def send_media_passive(entry, data, kind, caption=""):
    """c2c/group 富媒体被动回复：上传 + msg_type=7 发送。返回 (ok, response)。"""
    file_info = await upload_rich_media(entry, data, MEDIA_KIND_TO_FILE_TYPE[kind])
    body = {
        "content": caption or "",
        "msg_id": entry.get("msg_id"),
        "msg_type": 7,
        "msg_seq": random_msg_seq(),
        "media": {"file_info": file_info},
    }
    return await post_passive_message(entry, body)


MAX_DOWNLOAD_VIDEO_BYTES = 200 * 1024 * 1024  # 接收视频附件上限 200 MB（流式落盘，不进内存）
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _classify_attachment(att):
    """按 content_type 判定附件类型；缺失时退回看文件名后缀。返回 image/video/None。"""
    ctype = (att.get("content_type") or "").lower()
    if ctype.startswith("image/"):
        return "image"
    if ctype.startswith("video/"):
        return "video"
    name = (att.get("filename") or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return None


async def _download_video(url, att):
    """流式下载视频附件到 queue/media/，返回本地 Path。按魔数定后缀，识别不了则用文件名后缀。"""
    name_hint = (att.get("filename") or "").lower()
    hint_ext = name_hint.rsplit(".", 1)[-1] if "." in name_hint else ""
    tmp = MEDIA_DIR / f".tmp-{uuid.uuid4().hex[:8]}"
    total = 0
    head = b""
    try:
        async with make_aiohttp_session() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    async for chunk in resp.content.iter_chunked(256 * 1024):
                        total += len(chunk)
                        if total > MAX_DOWNLOAD_VIDEO_BYTES:
                            raise ValueError(f"video too large: > {MAX_DOWNLOAD_VIDEO_BYTES}")
                        if len(head) < 16:
                            head += chunk[: 16 - len(head)]
                        f.write(chunk)
        fmt = detect_video_format(head)
        if fmt == "mp4" and hint_ext == "mov":
            fmt = "mov"
        if not fmt:
            fmt = hint_ext if hint_ext in VIDEO_EXTS else "mp4"
        name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.{fmt}"
        path = MEDIA_DIR / name
        tmp.rename(path)
        return path
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def download_media(attachments):
    """下载消息中的图片/视频附件到 queue/media/，返回本地路径列表。

    图片上限 15 MB（整读校验魔数）；视频流式落盘、上限 200 MB。
    超时/失败不阻断入队，失败信息写回附件 download_error（HANDOVER 7.2）。
    """
    ensure_queue_dirs()
    saved = []
    for att in attachments or []:
        kind = _classify_attachment(att)
        if kind is None:
            continue
        url = att.get("url")
        if not url:
            continue
        try:
            if kind == "video":
                path = await _download_video(url, att)
            else:
                data = await fetch_image_bytes(url)
                if len(data) > MAX_MEDIA_BYTES:
                    raise ValueError(f"media too large: {len(data)}")
                fmt = detect_image_format(data)
                if not fmt:
                    raise ValueError("unsupported image format")
                name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.{fmt}"
                path = MEDIA_DIR / name
                path.write_bytes(data)
            saved.append(str(path))
        except Exception as e:  # 不阻断入队
            att["download_error"] = str(e)
    return saved
