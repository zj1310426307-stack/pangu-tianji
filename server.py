from argparse import ArgumentParser
import os
from pathlib import Path
import sys
from threading import Timer
import webbrowser


# Preserve the launcher path (including the supported ASCII junction) so local
# SQLite stores remain usable on legacy-codepage Windows Python runtimes.
PROJECT_ROOT = Path(os.path.abspath(__file__)).parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import uvicorn

from ashare_agent.api.app import create_app


def main() -> None:
    """Start the desktop-only server or an explicitly enabled trusted-LAN mobile mode."""
    parser = ArgumentParser(description="启动盘古·天机本地网页")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--port", type=int, default=8765, help="本机端口")
    parser.add_argument(
        "--mobile",
        action="store_true",
        help="显式启用受JWT保护的局域网移动助手并监听0.0.0.0",
    )
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/mobile/" if args.mobile else f"http://127.0.0.1:{args.port}"
    host = "0.0.0.0" if args.mobile else "127.0.0.1"
    if args.mobile:
        print("[盘古·天机] 移动助手已启用，仅限你信任的家庭/办公局域网，禁止直接暴露到公网。")
        print(f"[盘古·天机] 本机配对入口：{url}")
        print(
            f"[盘古·天机] 手机入口：http://<本机局域网IP>:{args.port}/mobile/"
        )
        if not os.getenv("PANGU_MOBILE_JWT_SECRET", "").strip():
            print("[盘古·天机] 未配置持久JWT密钥：本次使用运行期随机密钥，重启后手机需重新配对。")
    if not args.no_browser:
        Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        create_app(PROJECT_ROOT, mobile_enabled=args.mobile, port=args.port), host=host,
        port=args.port, log_level="info",
    )


if __name__ == "__main__":
    main()
