"""Agent Service 启动入口（供 Electron 子进程使用）。

手动管理 uvicorn.Server：端口为 0（随机）时，startup 完成后打印一行
`desktopmate_ready` JSON（含 auth_token 与实际 port），Electron 主进程解析后注入渲染进程。

用法：
    python run.py                        # 默认 127.0.0.1:0（随机端口）
    DESKTOPMATE_PORT=8000 python run.py  # 固定端口
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn

from desktopmate_agent import __version__
from desktopmate_agent.main import create_app


async def main() -> None:
    host = os.getenv("DESKTOPMATE_HOST", "127.0.0.1")
    port = int(os.getenv("DESKTOPMATE_PORT", "0"))
    app = create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    config.load()
    server.lifespan = config.lifespan_class(config)
    await server.startup()
    # 实际绑定端口（端口为 0 时由操作系统分配）
    actual_port = port
    if server.servers:
        sockets = server.servers[0].sockets
        if sockets:
            actual_port = sockets[0].getsockname()[1]
    print(
        json.dumps(
            {
                # S8 规格字段
                "type": "desktopmate_ready",
                "host": host,
                "port": actual_port,
                "token": app.state.auth_token,
                "data_dir": str(app.state.settings.app_data_dir),
                # 兼容字段（Electron agent-service.cjs 解析 event/auth_token/port）
                "event": "desktopmate_ready",
                "auth_token": app.state.auth_token,
                "version": __version__,
            }
        ),
        flush=True,
    )
    # 手动进入 serve 主循环（serve() 会重建 lifespan 并重复 startup）
    await server.main_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
