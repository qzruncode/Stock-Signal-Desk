# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 主调度程序
===================================

职责：
1. 协调各模块完成股票分析流程
2. 实现低并发的线程池调度
3. 全局异常处理，确保单股失败不影响整体
4. 提供命令行入口

使用方式：
    python main.py              # 正常运行
    python main.py --debug      # 调试模式
    python main.py --dry-run    # 仅获取数据不分析

交易理念（已融入分析）：
- 严进策略：不追高，乖离率 > 5% 不买入
- 趋势交易：只做 MA5>MA10>MA20 多头排列
- 效率优先：关注筹码集中度好的股票
- 买点偏好：缩量回踩 MA5/MA10 支撑
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import dotenv_values
from src.config import setup_env

_INITIAL_PROCESS_ENV = dict(os.environ)
setup_env()

# 代理配置 - 通过 USE_PROXY 环境变量控制，默认关闭
# GitHub Actions 环境自动跳过代理配置
if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
    # 本地开发环境，启用代理（可在 .env 中配置 PROXY_HOST 和 PROXY_PORT）
    proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
    proxy_port = os.getenv("PROXY_PORT", "10809")
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["http_proxy"] = proxy_url
    os.environ["https_proxy"] = proxy_url

import argparse
import logging
import sys
import time
from datetime import datetime

from src.webui_frontend import prepare_webui_frontend_assets
from src.config import get_config, Config
from src.logging_config import setup_logging


logger = logging.getLogger(__name__)
_RUNTIME_ENV_FILE_KEYS = set()


def _get_active_env_path() -> Path:
    env_file = os.getenv("ENV_FILE")
    if env_file:
        return Path(env_file)
    return Path(__file__).resolve().parent / ".env"


def _read_active_env_values() -> Optional[Dict[str, str]]:
    env_path = _get_active_env_path()
    if not env_path.exists():
        return {}

    try:
        values = dotenv_values(env_path)
    except Exception as exc:  # pragma: no cover - defensive branch
        logger.warning("读取配置文件 %s 失败，继续沿用当前环境变量: %s", env_path, exc)
        return None

    return {str(key): "" if value is None else str(value) for key, value in values.items() if key is not None}


_ACTIVE_ENV_FILE_VALUES = _read_active_env_values() or {}
_RUNTIME_ENV_FILE_KEYS = {key for key in _ACTIVE_ENV_FILE_VALUES if key not in _INITIAL_PROCESS_ENV}

# setup_env() already ran at import time above.
_env_bootstrapped = True


def _bootstrap_environment() -> None:
    """Load .env and apply optional local proxy settings.

    Guarded to be idempotent so it can safely be called from lazy-import
    paths used by API / bot consumers.
    """
    global _env_bootstrapped
    if _env_bootstrapped:
        return

    from src.config import setup_env

    setup_env()

    if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("USE_PROXY", "false").lower() == "true":
        proxy_host = os.getenv("PROXY_HOST", "127.0.0.1")
        proxy_port = os.getenv("PROXY_PORT", "10809")
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ["http_proxy"] = proxy_url
        os.environ["https_proxy"] = proxy_url

    _env_bootstrapped = True


def _setup_bootstrap_logging(debug: bool = False) -> None:
    """Initialize stderr-only logging before config is loaded.

    File handlers are deferred until ``config.log_dir`` is known (via the
    subsequent ``setup_logging()`` call) so that healthy runs never create
    log files in a hard-coded directory.
    """
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(handler)


def _setup_runtime_logging(log_dir: str, debug: bool = False) -> bool:
    """Switch to configured logging, falling back to console on file I/O errors."""
    try:
        setup_logging(log_prefix="stock_analysis", debug=debug, log_dir=log_dir)
        return True
    except OSError as exc:
        logger.warning(
            "文件日志初始化失败，已降级为控制台日志输出；日志目录 %r 当前不可写或不可创建: %s。"
            "官方 Docker 镜像启动入口会自动修复默认挂载目录权限；若仍失败，"
            "请检查是否使用了 --user、只读挂载、rootless Docker 或 NFS 等限制写入的环境。",
            log_dir,
            exc,
        )
        return False


def _reload_env_file_values_preserving_overrides() -> None:
    """Refresh `.env`-managed env vars without clobbering process env overrides."""
    global _RUNTIME_ENV_FILE_KEYS

    latest_values = _read_active_env_values()
    if latest_values is None:
        return

    managed_keys = {key for key in latest_values if key not in _INITIAL_PROCESS_ENV}

    for key in _RUNTIME_ENV_FILE_KEYS - managed_keys:
        os.environ.pop(key, None)

    for key in managed_keys:
        os.environ[key] = latest_values[key]

    _RUNTIME_ENV_FILE_KEYS = managed_keys


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="A股自选股智能分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                    # 启动 Web 服务
  python main.py --debug            # 调试模式
  python main.py --serve-only       # 仅启动 Web 服务
        """,
    )

    parser.add_argument("--debug", action="store_true", help="启用调试模式，输出详细日志")

    parser.add_argument("--webui", action="store_true", help="启动 Web 管理界面")

    parser.add_argument("--webui-only", action="store_true", help="仅启动 Web 服务")

    parser.add_argument("--serve", action="store_true", help="启动 FastAPI 后端服务")

    parser.add_argument("--serve-only", action="store_true", help="仅启动 FastAPI 后端服务")

    parser.add_argument("--port", type=int, default=8000, help="FastAPI 服务端口（默认 8000）")

    parser.add_argument("--host", type=str, default="0.0.0.0", help="FastAPI 服务监听地址（默认 0.0.0.0）")

    return parser.parse_args()


def start_api_server(host: str, port: int, config: Config) -> None:
    """
    在后台线程启动 FastAPI 服务

    Args:
        host: 监听地址
        port: 监听端口
        config: 配置对象
    """
    import threading
    import uvicorn

    def run_server():
        level_name = (config.log_level or "INFO").lower()
        uvicorn.run(
            "api.app:app",
            host=host,
            port=port,
            log_level=level_name,
            log_config=None,
        )

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    logger.info(f"FastAPI 服务已启动: http://{host}:{port}")


def main() -> int:
    """
    主入口函数

    Returns:
        退出码（0 表示成功）
    """
    # 解析命令行参数
    args = parse_arguments()

    # 在配置加载前先初始化 bootstrap 日志，确保早期失败也能落盘
    try:
        _setup_bootstrap_logging(debug=args.debug)
    except Exception as exc:
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            stream=sys.stderr,
        )
        logger.warning("Bootstrap 日志初始化失败，已回退到 stderr: %s", exc)

    # 加载配置（在 bootstrap logging 之后执行，确保异常有日志）
    try:
        config = get_config()
    except Exception as exc:
        logger.exception("加载配置失败: %s", exc)
        return 1

    # 配置日志（输出到控制台和文件）
    try:
        _setup_runtime_logging(config.log_dir, debug=args.debug)
    except Exception as exc:
        logger.exception("切换到配置日志目录失败: %s", exc)
        return 1

    logger.info("=" * 60)
    logger.info("A股自选股智能分析系统 启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 验证配置
    warnings = config.validate()
    for warning in warnings:
        logger.warning(warning)

    # === 处理 --webui / --webui-only 参数，映射到 --serve / --serve-only ===
    if args.webui:
        args.serve = True
    if args.webui_only:
        args.serve_only = True

    # 兼容旧版 WEBUI_ENABLED 环境变量
    if config.webui_enabled and not (args.serve or args.serve_only):
        args.serve = True

    # === 启动 Web 服务 (如果启用) ===
    start_serve = (args.serve or args.serve_only) and os.getenv("GITHUB_ACTIONS") != "true"

    # 兼容旧版 WEBUI_HOST/WEBUI_PORT：如果用户未通过 --host/--port 指定，则使用旧变量
    if start_serve:
        if args.host == "0.0.0.0" and os.getenv("WEBUI_HOST"):
            args.host = os.getenv("WEBUI_HOST")
        if args.port == 8000 and os.getenv("WEBUI_PORT"):
            args.port = int(os.getenv("WEBUI_PORT"))

    if start_serve:
        if not prepare_webui_frontend_assets():
            logger.warning("前端静态资源未就绪，继续启动 FastAPI 服务（Web 页面可能不可用）")
        try:
            start_api_server(host=args.host, port=args.port, config=config)
        except Exception as e:
            logger.error(f"启动 FastAPI 服务失败: {e}")
            return 1

    logger.info("模式: Web 服务")
    logger.info(f"Web 服务运行中: http://{args.host}:{args.port}")
    logger.info("通过 /api/v1/analysis/analyze 接口触发分析")
    logger.info(f"API 文档: http://{args.host}:{args.port}/docs")
    logger.info("按 Ctrl+C 退出...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n用户中断，程序退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
