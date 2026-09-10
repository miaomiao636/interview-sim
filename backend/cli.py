"""interview-sim 本地工作台命令行入口。"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from . import config


HOST = "127.0.0.1"


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def find_available_port(preferred: int, attempts: int = 20) -> int:
    for port in range(preferred, preferred + attempts):
        if _is_port_available(port):
            return port
    raise RuntimeError(f"无法在 {preferred}-{preferred + attempts - 1} 找到可用端口")


def _open_browser_when_ready(url: str) -> None:
    webbrowser.open(url, new=2)


def run_web(args: argparse.Namespace) -> int:
    port = find_available_port(args.port)
    url = f"http://{HOST}:{port}"
    print(f"INTERVIEW_SIM_URL={url}", flush=True)
    if port != args.port:
        print(f"端口 {args.port} 已被占用，已自动切换到 {port}。", flush=True)
    print("面试工作台正在启动，请不要直接打开 frontend/index.html。", flush=True)
    if not args.no_open:
        threading.Timer(0.8, _open_browser_when_ready, args=(url,)).start()
    uvicorn.run("backend.main:app", host=HOST, port=port, reload=False)
    return 0


def show_status(args: argparse.Namespace) -> int:
    url = f"http://{HOST}:{args.port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            health = json.loads(response.read().decode("utf-8"))
        health["url"] = url.removesuffix("/api/health")
        print(json.dumps(health, ensure_ascii=False))
        return 0
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        print(json.dumps({"status": "stopped", "url": url.removesuffix("/api/health")}, ensure_ascii=False))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="interview-sim", description="启动本地 AI 面试训练工作台")
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web", help="启动服务并打开本地网页")
    web.add_argument("--port", type=int, default=config.PORT, help=f"首选端口（默认 {config.PORT}）")
    web.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    web.set_defaults(handler=run_web)

    status = subparsers.add_parser("status", help="检查本地工作台状态")
    status.add_argument("--port", type=int, default=config.PORT)
    status.set_defaults(handler=show_status)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
