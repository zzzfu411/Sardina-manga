#!/usr/bin/env python3
"""Start the local Sardina service independently of the terminal session."""
import argparse
import http.client
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'output' / 'runtime'
PID_FILE = RUNTIME / 'sardina-server.pid'
LOG_FILE = RUNTIME / 'sardina-server.log'
PORT = 8765
URL = f'http://127.0.0.1:{PORT}/'


def ready():
    connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=1)
    try:
        connection.request('GET', '/api/config')
        response = connection.getresponse()
        response.read()
        return response.status == 200 and response.getheader('Server', '').startswith(('Sardina/', 'revYunman/'))
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def port_in_use():
    with socket.socket() as connection:
        connection.settimeout(1)
        return connection.connect_ex(('127.0.0.1', PORT)) == 0


def owned_pid():
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid <= 1:
            return None
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, check=False).stdout
        return pid if str(ROOT / 'server.py') in command else None
    except (OSError, ValueError):
        return None


def start():
    if ready():
        print(f'Sardina 已在运行：{URL}')
        return
    if port_in_use():
        raise RuntimeError(f'端口 {PORT} 已被其他服务占用；未停止该服务。')
    python = ROOT / '.venv' / 'bin' / 'python'
    if not python.is_file():
        raise RuntimeError('缺少项目环境，请先在项目目录执行 uv sync。')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open('ab', buffering=0) as log:
        process = subprocess.Popen([str(python), str(ROOT / 'server.py'), '--port', str(PORT)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            close_fds=True, start_new_session=True)
    PID_FILE.write_text(str(process.pid) + '\n')
    for _ in range(30):
        if ready():
            print(f'Sardina 已启动：{URL}\n日志：{LOG_FILE}')
            return
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise RuntimeError(f'启动失败，请查看日志：{LOG_FILE}')
        time.sleep(0.1)
    raise RuntimeError(f'启动仍未就绪，请查看日志：{LOG_FILE}')


def stop():
    pid = owned_pid()
    if pid is None:
        raise RuntimeError('没有找到此启动器管理的进程；未停止其他服务。')
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(30):
        if not port_in_use():
            PID_FILE.unlink(missing_ok=True)
            print('Sardina 已停止。')
            return
        time.sleep(0.1)
    raise RuntimeError('已请求停止，端口仍在使用；未强制终止其他进程。')


def main():
    parser = argparse.ArgumentParser(description='Sardina 本地服务')
    parser.add_argument('action', choices=('start', 'status', 'stop'), nargs='?', default='start')
    args = parser.parse_args()
    try:
        if args.action == 'start':
            start()
        elif args.action == 'stop':
            stop()
        elif ready():
            print(f'Sardina 正在运行：{URL}')
        else:
            print('Sardina 未运行。')
            return 1
    except (OSError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
