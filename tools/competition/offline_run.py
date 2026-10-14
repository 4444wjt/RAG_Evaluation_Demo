"""本机无外网试跑：后端进程只允许连回环地址，其余连接一律拒绝并记录。

  python tools/competition/offline_run.py serve   --log <拦截记录.jsonl> [--port 8000]
  python tools/competition/offline_run.py gateway --upstream http://127.0.0.1:11434 [--port 11435]

serve：先给 socket 装守卫，再在同一进程内启动 API（backend.api.app:create_app，单 worker）。
  守卫只放行 127.0.0.0/8、::1 与 localhost；连接外部地址、解析外部主机名、发 UDP 都会被拒绝，
  每次都记一行 JSON（时间、调用、目标、端口、发起的代码位置）。这是进程级拦截，不等于物理断网。
gateway：本机 OpenAI 兼容网关，原样转发到上游（如 Ollama），只在请求里补 "reasoning_effort": "none"。
  平台调用模型时不传思考开关；带思考的小模型单次调用接近超时，用网关关掉思考，平台代码不改。
两者都只监听 127.0.0.1，只用标准库。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import socket
import sys
import threading
import traceback
from urllib.parse import urlsplit
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LOCAL_NAMES = {'localhost', 'localhost.localdomain'}


def is_loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode('ascii', 'replace')
    host = str(host).strip('[]').split('%')[0]
    if host.lower() in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin() -> str:
    """发起连接的代码位置：跳过本文件与 socket 模块，只给相对路径，不暴露本机目录。"""
    for frame in reversed(traceback.extract_stack()[:-3]):
        name = frame.filename.replace('\\', '/')
        if name.endswith(('/offline_run.py', '/socket.py')):
            continue
        if 'site-packages/' in name:
            name = name.split('site-packages/', 1)[1]
        elif name.startswith(ROOT.as_posix() + '/'):
            name = name[len(ROOT.as_posix()) + 1:]
        else:
            name = Path(name).name
        return f'{name}:{frame.lineno}'
    return 'unknown'


class Guard:
    """进程内的出站守卫。install 时保存当时的函数，放行回环目标时原样调用。"""

    def __init__(self, log: Path):
        self.log = Path(log)
        self.lock = threading.Lock()
        self.blocked = 0

    def record(self, call: str, host, port) -> None:
        entry = {'time': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'call': call,
                 'host': host.decode('ascii', 'replace') if isinstance(host, bytes) else str(host),
                 'port': port, 'origin': _origin()}
        with self.lock:
            self.blocked += 1
            with self.log.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _deny(self, call: str, host, port):
        self.record(call, host, port)
        raise OSError(f'offline_run: outbound connection blocked ({call} {host}:{port})')

    def install(self) -> None:
        guard = self
        getaddrinfo, create_connection = socket.getaddrinfo, socket.create_connection
        connect, connect_ex, sendto = socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto

        def checked(call, address):
            if isinstance(address, tuple) and len(address) >= 2 and not is_loopback(address[0]):
                guard._deny(call, address[0], address[1])

        def guarded_getaddrinfo(host, port, *args, **kwargs):
            if not is_loopback(host):
                guard._deny('getaddrinfo', host, port)
            return getaddrinfo(host, port, *args, **kwargs)

        def guarded_create_connection(address, *args, **kwargs):
            checked('create_connection', address)
            return create_connection(address, *args, **kwargs)

        def guarded_connect(self, address):
            checked('connect', address)
            return connect(self, address)

        def guarded_connect_ex(self, address):
            checked('connect_ex', address)
            return connect_ex(self, address)

        def guarded_sendto(self, data, *args):
            checked('sendto', args[-1] if args else None)
            return sendto(self, data, *args)

        socket.getaddrinfo = guarded_getaddrinfo
        socket.create_connection = guarded_create_connection
        socket.socket.connect = guarded_connect
        socket.socket.connect_ex = guarded_connect_ex
        socket.socket.sendto = guarded_sendto


def serve(log: Path, port: int) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('', encoding='utf-8')
    Guard(log).install()
    sys.path.insert(0, str(ROOT))
    import uvicorn
    uvicorn.run('backend.api.app:create_app', factory=True, host='127.0.0.1', port=port, workers=1)


def make_gateway(upstream: str):
    parts = urlsplit(upstream)
    if parts.scheme != 'http' or not is_loopback(parts.hostname):
        raise SystemExit('gateway 只转发到本机回环地址上的 http 上游')

    class Handler(BaseHTTPRequestHandler):
        def _forward(self, body: bytes | None):
            if body:
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    payload.setdefault('reasoning_effort', 'none')
                    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() in ('content-type', 'authorization', 'accept')}
            request = urllib.request.Request(upstream.rstrip('/') + self.path, data=body,
                                             headers=headers, method=self.command)
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    status, data, kind = response.status, response.read(), response.headers.get('Content-Type')
            except urllib.error.HTTPError as error:
                status, data, kind = error.code, error.read(), error.headers.get('Content-Type')
            self.send_response(status)
            self.send_header('Content-Type', kind or 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._forward(None)

        def do_POST(self):
            self._forward(self.rfile.read(int(self.headers.get('Content-Length') or 0)))

        def log_message(self, *args):
            pass

    return Handler


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('serve')
    run.add_argument('--log', type=Path, required=True, help='拦截记录（JSONL）')
    run.add_argument('--port', type=int, default=8000)
    gate = commands.add_parser('gateway')
    gate.add_argument('--upstream', default='http://127.0.0.1:11434')
    gate.add_argument('--port', type=int, default=11435)
    args = parser.parse_args(argv)
    if args.command == 'serve':
        serve(args.log, args.port)
    else:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), make_gateway(args.upstream))
        print(f'gateway 127.0.0.1:{args.port} -> {args.upstream}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
