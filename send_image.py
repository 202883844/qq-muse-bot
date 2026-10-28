"""发送一张图片被动回复，并归档队列文件（HANDOVER 7.1）。

用法:
  .venv/bin/python send_image.py queue/processing/<file>.json <本地图片|http(s)地址> "配文"
只发一张；上限 10 MB；仅 PNG/JPEG/GIF/WEBP（按文件头魔数校验）。
c2c/group: 先走 v2 文件接口拿 file_info，再发 msg_type=7。
channel/dm: multipart form-data，字段 file_image。
退出码同 send_reply.py。
"""
import argparse
import asyncio
import base64
import json
import sys

import aiohttp

import qqbot_common as qc
import quota_guard


async def _send(entry, image_source, caption, no_archive):
    try:
        data = await qc.fetch_image_bytes(image_source)
        qc.validate_send_image(data)
    except Exception as e:
        print(json.dumps({"sent": False, "error": str(e)}, ensure_ascii=False))
        qc.archive_queue_file(entry["_queue_path"], success=False, no_archive=no_archive)
        return 1
    if qc.passive_window_expired(entry):
        print("warning: passive window may have expired, still trying", file=sys.stderr)
    t = entry.get("type")
    try:
        if t in ("c2c", "group"):
            ok, resp = await qc.send_media_passive(entry, data, "image", caption)
        elif t in ("channel", "dm"):
            token = await qc.get_access_token()
            form = aiohttp.FormData()
            form.add_field("file_image", data, filename="image", content_type="application/octet-stream")
            form.add_field("msg_id", entry.get("msg_id") or "")
            if caption:
                form.add_field("content", caption)
            url = qc.API_BASE + qc.target_path(entry)
            async with qc.make_aiohttp_session() as session:
                async with session.post(url, data=form, headers=qc.auth_headers(token)) as r:
                    try:
                        resp_data = await r.json()
                    except Exception:
                        resp_data = {"raw": await r.text()}
                    ok, resp = r.status < 400, {"status": r.status, **(resp_data if isinstance(resp_data, dict) else {})}
        else:
            raise ValueError(f"unsupported type: {t}")
    except Exception as e:
        ok, resp = False, {"error": str(e)}
    print(json.dumps({"sent": ok, "response": resp}, ensure_ascii=False))
    qc.archive_queue_file(entry["_queue_path"], success=ok, no_archive=no_archive)
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("queue_file")
    p.add_argument("image")
    p.add_argument("caption", nargs="?", default="")
    p.add_argument("--no-archive", action="store_true")
    p.add_argument("--ignore-quota", action="store_true")
    args = p.parse_args()
    entry = qc.load_queue_file(args.queue_file)
    entry["_queue_path"] = args.queue_file
    if not args.ignore_quota and quota_guard.is_over_quota():
        print(json.dumps({"sent": False, "error": "quota full"}, ensure_ascii=False))
        qc.archive_queue_file(args.queue_file, success=False, no_archive=args.no_archive)
        return 3
    return asyncio.run(_send(entry, args.image, args.caption, args.no_archive))


if __name__ == "__main__":
    sys.exit(main())
