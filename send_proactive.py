"""主动消息（不带 msg_id），仅兜底（HANDOVER 第 9 节）。

仅支持 group/c2c。注意：机器人默认没有群主动消息权限，实测返回
40034105，不要把主动消息当可靠补发手段。
用法:
  .venv/bin/python send_proactive.py <队列文件.json> <图片路径> "配文"
  .venv/bin/python send_proactive.py <队列文件.json> --text-only "文字"
"""
import argparse
import asyncio
import base64
import json
import sys

import qqbot_common as qc


async def _run(args):
    entry = qc.load_queue_file(args.queue_file)
    t = entry.get("type")
    if t not in ("c2c", "group"):
        print(json.dumps({"sent": False, "error": f"proactive unsupported for type {t}"}, ensure_ascii=False))
        return 1
    token = await qc.get_access_token()
    target_id = entry.get("author_id") if t == "c2c" else entry.get("group_id")
    kind = "users" if t == "c2c" else "groups"
    headers = qc.auth_headers(token)
    try:
        if args.text_only:
            body = {"content": args.text_only, "msg_type": 0, "msg_seq": qc.random_msg_seq()}
            async with qc.make_aiohttp_session() as session:
                async with session.post(f"{qc.API_BASE}/v2/{kind}/{target_id}/messages", json=body, headers=headers) as r:
                    resp = await r.json()
                    ok = r.status < 400
        else:
            data = await qc.fetch_image_bytes(args.image)
            qc.validate_send_image(data)
            async with qc.make_aiohttp_session() as session:
                async with session.post(
                    f"{qc.API_BASE}/v2/{kind}/{target_id}/files",
                    json={"file_type": 1, "file_data": base64.b64encode(data).decode(), "srv_send_msg": False},
                    headers=headers,
                ) as r:
                    file_resp = await r.json()
                    if r.status >= 400 or "file_info" not in file_resp:
                        raise RuntimeError(f"file upload failed: {file_resp}")
                body = {"content": args.caption or "", "msg_type": 7, "msg_seq": qc.random_msg_seq(),
                        "media": {"file_info": file_resp["file_info"]}}
                async with session.post(f"{qc.API_BASE}/v2/{kind}/{target_id}/messages", json=body, headers=headers) as r:
                    resp = await r.json()
                    ok = r.status < 400
    except Exception as e:
        print(json.dumps({"sent": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"sent": ok, "response": resp}, ensure_ascii=False))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("queue_file")
    p.add_argument("image", nargs="?")
    p.add_argument("caption", nargs="?", default="")
    p.add_argument("--text-only")
    args = p.parse_args()
    if not args.text_only and not args.image:
        print(json.dumps({"sent": False, "error": "need image or --text-only"}, ensure_ascii=False))
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
