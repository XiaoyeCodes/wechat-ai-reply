import argparse
from pathlib import Path
import threading
import webbrowser

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description='本机微信聊天工作台')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo', action='store_true', help='独立演示环境，不连接真实微信')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not args.no_browser:
        threading.Timer(1.5, lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    app = create_app(args.data_dir, demo=args.demo)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port,
                                          log_level='info', timeout_graceful_shutdown=5))
    app.state.shutdown_callback = lambda: setattr(server, 'should_exit', True)
    server.run()


if __name__ == '__main__':
    main()
