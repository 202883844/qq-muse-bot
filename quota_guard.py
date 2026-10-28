"""每周用量限额保护（HANDOVER 第 10 节）。

用量达 100% 时 bot.py 停止入队、发送脚本停止发送，只允许一条固定暂停通知。
状态缓存 .quota_cache.json，最多每 5 分钟刷新一次。
fetch_usage_percent 优先级：QUOTA_USAGE_PERCENT 静态值 -> QUOTA_USAGE_URL ->
两者都没有返回 None（未知，视为未超限，机器人正常工作）。
"""
import json
import os
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / ".quota_cache.json"
CACHE_TTL = 5 * 60

PAUSE_NOTICE = "本周用量已达上限，机器人暂停回复，额度重置后自动恢复。"


def _from_url(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    # 容器内必须走代理：urllib 默认读环境代理变量
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict):
        if "percent" in data:
            return float(data["percent"])
        if "usage_percent" in data:
            return float(data["usage_percent"])
        if "used" in data and "limit" in data and float(data["limit"]) > 0:
            return float(data["used"]) / float(data["limit"]) * 100.0
    return None


def fetch_usage_percent():
    static = os.environ.get("QUOTA_USAGE_PERCENT", "").strip()
    if static:
        try:
            return float(static)
        except ValueError:
            pass
    url = os.environ.get("QUOTA_USAGE_URL", "").strip()
    if url:
        try:
            return _from_url(url)
        except Exception:
            return None
    return None


def current_percent(use_cache=True):
    now = time.time()
    if use_cache and CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if now - cache.get("ts", 0) < CACHE_TTL:
                return cache.get("percent")
        except Exception:
            pass
    percent = fetch_usage_percent()
    try:
        CACHE_PATH.write_text(json.dumps({"percent": percent, "ts": now}), encoding="utf-8")
    except Exception:
        pass
    return percent


def is_over_quota():
    percent = current_percent()
    return percent is not None and percent >= 100.0


if __name__ == "__main__":
    # 延迟加载 .env（直接运行时）
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except Exception:
        pass
    p = current_percent(use_cache=False)
    print(json.dumps({
        "percent": p,
        "over_quota": (p is not None and p >= 100.0),
        "source": "QUOTA_USAGE_PERCENT" if os.environ.get("QUOTA_USAGE_PERCENT") else (
            "QUOTA_USAGE_URL" if os.environ.get("QUOTA_USAGE_URL") else "none (unknown = not over)"),
    }, ensure_ascii=False))
