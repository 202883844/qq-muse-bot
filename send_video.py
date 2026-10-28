"""发送一个视频被动回复，并归档队列文件。

用法:
  .venv/bin/python send_video.py queue/processing/<file>.json <本地视频|http(s)地址> "配文"
仅 c2c/group：走富媒体两步（POST /v2/{users|groups}/<id>/files, file_type=2，
再以 msg_type=7 发送）。channel/dm 不支持此路径，会明确报错。
格式仅 MP4/MOV/WebM（魔数校验），单次上限 20 MB（超大文件需要分片上传，本脚本不处理）。
退出码同 send_reply.py：0 成功 / 1 失败 / 3 限额已满。
"""
import argparse
import asyncio
import json
import sys

import qqbot_common as qc
import quota_guard


async def _send(entry, source, caption, no_archive):
    try:
        data = await qc.fetch_image_bytes(source)  # 通用字节抓取（本地/直连/转抓）
        qc.validate_media(data, "video")
    except Exception as e:
        print(json.dumps({"sent": False, "error": str(e)}, ensure_ascii=False))
        qc.archive_queue_file(entry["_queue_path"], success=False, no_archive=no_archive)
        return 1
    if qc.passive_window_expired(entry):
        print("warning: passive window may have expired, still trying", file=sys.stderr)
    try:
        ok, resp = await qc.send_media_passive(entry, data, "video", caption)
    except Exception as e:
        ok, resp = False, {"error": str(e)}
    print(json.dumps({"sent": ok, "response": resp}, ensure_ascii=False))
    qc.archive_queue_file(entry["_queue_path"], success=ok, no_archive=no_archive)
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("queue_file")
    p.add_argument("video")
    p.add_argument("caption", nargs="?", default="")
    p.add_argument("--no-archive", action="store_true")
    p.add_argument("--ignore-quota", action="store_true")
    args = p.parse_args()
    entry = qc.load_queue_file(args.queue_file)
    entry["_queue_path"] = args.queue_file
    if entry.get("type") not in ("c2c", "group"):
        print(json.dumps({"sent": False, "error": f"video passive send unsupported for type {entry.get('type')}"}, ensure_ascii=False))
        return 1
    if not args.ignore_quota and quota_guard.is_over_quota():
        print(json.dumps({"sent": False, "error": "quota full"}, ensure_ascii=False))
        qc.archive_queue_file(args.queue_file, success=False, no_archive=args.no_archive)
        return 3
    return asyncio.run(_send(entry, args.video, args.caption, args.no_archive))


if __name__ == "__main__":
    sys.exit(main())
