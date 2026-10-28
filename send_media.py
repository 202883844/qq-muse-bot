"""通用富媒体被动回复：按类型分发到图片/视频发送逻辑。

用法:
  .venv/bin/python send_media.py queue/processing/<file>.json <媒体路径或URL> "配文" [--kind image|video]
不带 --kind 时按文件魔数自动判断（图片走 send_image 逻辑，视频走 send_video 逻辑）。
"""
import argparse
import asyncio
import json
import sys

import qqbot_common as qc


def _detect_kind(data):
    if qc.detect_image_format(data):
        return "image"
    if qc.detect_video_format(data):
        return "video"
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("queue_file")
    p.add_argument("media")
    p.add_argument("caption", nargs="?", default="")
    p.add_argument("--kind", choices=["image", "video"])
    p.add_argument("--no-archive", action="store_true")
    p.add_argument("--ignore-quota", action="store_true")
    args = p.parse_args()

    import send_image
    import send_video

    # 先读字节判定类型（判定失败则交给对应脚本报错归档）
    async def _read():
        return await qc.fetch_image_bytes(args.media)

    try:
        data = asyncio.run(_read())
        kind = args.kind or _detect_kind(data)
    except Exception:
        kind = args.kind or "image"

    argv = [args.queue_file, args.media, args.caption]
    if args.no_archive:
        argv.append("--no-archive")
    if args.ignore_quota:
        argv.append("--ignore-quota")
    old_argv = sys.argv
    try:
        if kind == "video":
            sys.argv = ["send_video.py"] + argv
            return send_video.main()
        sys.argv = ["send_image.py"] + argv
        return send_image.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    sys.exit(main())
