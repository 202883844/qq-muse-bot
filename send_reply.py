"""发送一条文字被动回复，并归档队列文件（HANDOVER 第 6 节）。

用法:
  .venv/bin/python send_reply.py queue/processing/<file>.json "回复文本"
  .venv/bin/python send_reply.py queue/processing/<file>.json --text-file reply.txt
退出码: 0 成功 / 1 发送失败 / 2 参数缺失 / 3 限额已满拒绝发送
"""
import argparse
import asyncio
import json
import sys

import qqbot_common as qc
import quota_guard


async def _run(args):
    entry = qc.load_queue_file(args.queue_file)
    if args.text_file:
        text = open(args.text_file, encoding="utf-8").read()
    else:
        text = args.text
    if not text:
        print(json.dumps({"sent": False, "error": "missing reply text"}, ensure_ascii=False))
        return 2
    if not args.ignore_quota and quota_guard.is_over_quota():
        print(json.dumps({"sent": False, "error": "quota full"}, ensure_ascii=False))
        qc.archive_queue_file(args.queue_file, success=False, no_archive=args.no_archive)
        return 3
    if qc.passive_window_expired(entry):
        print("warning: passive window may have expired, still trying", file=sys.stderr)
    try:
        ok, resp = await qc.send_text_passive(entry, text)
    except Exception as e:
        ok, resp = False, {"error": str(e)}
    print(json.dumps({"sent": ok, "response": resp}, ensure_ascii=False))
    qc.archive_queue_file(args.queue_file, success=ok, no_archive=args.no_archive)
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("queue_file")
    p.add_argument("text", nargs="?")
    p.add_argument("--text-file")
    p.add_argument("--no-archive", action="store_true")
    p.add_argument("--ignore-quota", action="store_true")
    args = p.parse_args()
    if not args.text and not args.text_file:
        print(json.dumps({"sent": False, "error": "missing reply text"}, ensure_ascii=False))
        return 2
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
