"""本地工作台启动入口；只监听本机，不下载依赖。"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent
PORTS = range(8886, 8897)
INSTANCE_ID = hashlib.sha256(os.path.normcase(str(ROOT)).encode('utf-8')).hexdigest()


def valid_runtime_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                and parsed.port in PORTS and parsed.username is None
                and parsed.password is None and parsed.path in ('', '/')
                and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError):
        return False


def is_our_service(url):
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url.rstrip('/') + '/api/health', timeout=0.4) as response:
            payload = json.loads(response.read(4096))
        return (
            payload.get('status') == 'ok'
            and payload.get('application') == 'materials-silane-library'
            and payload.get('instance_id') == INSTANCE_ID
        )
    except (OSError, ValueError, AttributeError):
        return False


def existing_service():
    candidates = []
    try:
        url = json.loads((ROOT / 'logs/runtime.json').read_text(encoding='utf-8'))['url']
        if valid_runtime_url(url):
            candidates.append(url)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    candidates.extend(f'http://127.0.0.1:{port}' for port in PORTS)
    return next((url for url in dict.fromkeys(candidates) if is_our_service(url)), None)


class InstanceLock:
    """OS 文件锁，进程退出自动释放；文件存在本身不表示仍在运行。"""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        if handle.seek(0, 2) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle:
            self.handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def reserve_port(ports=PORTS):
    for port in ports:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', port))
            listener.listen(128)
            return listener, listener.getsockname()[1]
        except OSError:
            listener.close()
    raise RuntimeError('本机端口 8886–8896 均被占用，请关闭占用这些端口的程序后重试。')


def check_installation(root=ROOT):
    database = root / 'data/oer.sqlite'
    if not database.is_file() or database.stat().st_size == 0:
        raise RuntimeError('未找到非空数据库 data/oer.sqlite。请从备份恢复此文件，或按 README 的首次数据准备说明操作；启动器不会自动导入源数据。')
    if not (root / 'frontend/dist/index.html').is_file():
        raise RuntimeError('缺少网页构建文件。请在 frontend 目录执行 pnpm install --frozen-lockfile 和 pnpm build，详见 README。')


def record_runtime(url, destination=None):
    runtime = Path(destination or ROOT / 'logs/runtime.json')
    temporary = runtime.with_suffix('.tmp')
    temporary.write_text(json.dumps({'url': url, 'pid': os.getpid()}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(runtime)


def announce_ready(url, server, no_browser):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not server.should_exit:
        if is_our_service(url):
            record_runtime(url)
            logging.info('服务已就绪：%s', url)
            if not no_browser:
                webbrowser.open(url)
            return
        time.sleep(0.25)
    logging.error('服务未在 45 秒内通过健康检查。')
    server.should_exit = True
    show_error('服务启动超时，请查看 logs/launcher.log。', no_browser)


def show_error(message, no_browser):
    logging.error(message)
    if not no_browser and os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, 'OER 本地数据工作台', 0x10)


def main(argv=None):
    parser = argparse.ArgumentParser(description='OER 本地数据工作台')
    parser.add_argument('--no-browser', action='store_true', help='前台运行，不打开浏览器')
    args = parser.parse_args(argv)
    (ROOT / 'logs').mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / 'logs/launcher.log', encoding='utf-8', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    lock = InstanceLock(ROOT / 'logs/service.lock')
    listener = None
    try:
        url = existing_service()
        if url:
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        if not lock.acquire():
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                url = existing_service()
                if url:
                    if not args.no_browser:
                        webbrowser.open(url)
                    return 0
                time.sleep(0.5)
            raise RuntimeError('另一个启动进程尚未就绪。请查看 logs/launcher.log，稍后重试。')
        check_installation()
        listener, port = reserve_port()
        url = f'http://127.0.0.1:{port}'
        import uvicorn
        from backend.app import create_app
        # pythonw has no console streams; all service logs go to launcher.log.
        server = uvicorn.Server(uvicorn.Config(create_app(), host='127.0.0.1', port=port,
                                              log_config=None, access_log=False))
        threading.Thread(target=announce_ready, args=(url, server, args.no_browser), daemon=True).start()
        server.run(sockets=[listener])
        if not server.started:
            raise RuntimeError('服务未成功启动，请查看 logs/launcher.log。')
        return 0
    except Exception as exc:
        logging.exception('启动失败')
        show_error(str(exc), args.no_browser)
        return 1
    finally:
        if listener:
            listener.close()
        lock.release()


if __name__ == '__main__':
    sys.exit(main())
