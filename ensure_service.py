"""把模板渲染成 systemd 单元并安装（HANDOVER 第 4 节）。

无参数: 只在项目目录生成 qqbot-ws.service 供检查。
--install: 复制到 /etc/systemd/system/qqbot-ws.service 并 daemon-reload（需要 root）。
--enable: 在 --install 基础上 enable --now；没有 .env 时拒绝 --enable。
注意：渲染后的单元含带口令的代理地址，按敏感文件对待，不外发其内容。
"""
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE = BASE_DIR / "templates" / "qqbot-ws.service.tmpl"
RENDERED = BASE_DIR / "qqbot-ws.service"
SYSTEM_PATH = Path("/etc/systemd/system/qqbot-ws.service")
PROXY_KEYS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def render():
    tmpl = TEMPLATE.read_text(encoding="utf-8")
    lines = []
    for key in PROXY_KEYS:
        val = os.environ.get(key)
        if val:
            # 大小写变量都原样写入，保证不同库的代理读取习惯都能命中
            lines.append(f"Environment={key}={val}")
    proxy_block = ("\n".join(lines) + "\n") if lines else ""
    out = tmpl.replace("{BASE_DIR}", str(BASE_DIR)).replace("{PROXY_ENV_LINES}", proxy_block)
    RENDERED.write_text(out, encoding="utf-8")
    try:
        RENDERED.chmod(0o600)  # 含带口令代理地址
    except Exception:
        pass
    return RENDERED


def _run(cmd):
    print("+", " ".join(cmd))
    return subprocess.run(cmd).returncode


def main():
    args = set(sys.argv[1:])
    rendered = render()
    print(f"rendered: {rendered}")
    if "--install" in args or "--enable" in args:
        if os.geteuid() != 0:
            print("error: --install needs root", file=sys.stderr)
            return 1
        if "--enable" in args and not (BASE_DIR / ".env").exists():
            print("error: no .env, refusing --enable", file=sys.stderr)
            return 2
        SYSTEM_PATH.write_text(rendered.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            SYSTEM_PATH.chmod(0o600)
        except Exception:
            pass
        if _run(["systemctl", "daemon-reload"]) != 0:
            return 1
        print(f"installed: {SYSTEM_PATH}")
    if "--enable" in args:
        return _run(["systemctl", "enable", "--now", "qqbot-ws"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
