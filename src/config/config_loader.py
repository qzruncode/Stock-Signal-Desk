"""Environment-driven configuration construction.

This module owns the I/O-heavy loading phase so the public ``Config`` model
remains focused on state, validation, and compatibility helpers.
"""

import os


def load_config_from_env(
    cls,
    *,
    setup_env,
    resolve_proxy_and_configure_no_proxy,
    parse_env_int,
    parse_env_float,
    resolve_unified_llm_temperature,
    fundamental_stage_timeout_seconds_default,
):
    """从环境变量加载配置"""
    cls._capture_bootstrap_runtime_env_overrides()
    preexisting_report_language = os.environ.get("REPORT_LANGUAGE")

    setup_env()

    # Proxy setup (delegated to proxy_config for single-source-of-truth)
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    resolve_proxy_and_configure_no_proxy(http_proxy, https_proxy)

    # Parse stock list
    stock_list_str = cls._resolve_env_value("STOCK_LIST", default="", prefer_env_file=True)
    stock_list = [(c or "").strip().upper() for c in stock_list_str.split(",") if (c or "").strip()]
    if not stock_list:
        stock_list = ["600519", "000001", "300750"]

    # LLM 配置：模型/鉴权统一由 Anthropic 网关（ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL）
    # 决定，由 src.llm.anthropic_gateway 解析，Config 只保留正交生成参数。
    # 多供应商路由（LITELLM_MODEL/LLM_CHANNELS/LITELLM_CONFIG/各供应商 key）已退役。

    wechat_msg_type = os.getenv("WECHAT_MSG_TYPE", "markdown")
    wechat_msg_type_lower = wechat_msg_type.lower()
    wechat_max_bytes_env = os.getenv("WECHAT_MAX_BYTES")
    if wechat_max_bytes_env not in (None, ""):
        wechat_max_bytes = parse_env_int(
            wechat_max_bytes_env,
            2048 if wechat_msg_type_lower == "text" else 4000,
            field_name="WECHAT_MAX_BYTES",
            minimum=1,
        )
    else:
        wechat_max_bytes = 2048 if wechat_msg_type_lower == "text" else 4000

    report_language_raw = cls._resolve_report_language_env_value(preexisting_report_language)
    return cls(
        stock_list=stock_list,
        llm_temperature=resolve_unified_llm_temperature(""),
        llm_thinking_enabled=os.getenv("LLM_THINKING_ENABLED", "false").lower() == "true",
        llm_reasoning_effort=(os.getenv("LLM_REASONING_EFFORT") or "auto").strip().lower(),
        news_max_age_days=parse_env_int(
            os.getenv("NEWS_MAX_AGE_DAYS"), 3, field_name="NEWS_MAX_AGE_DAYS", minimum=1
        ),
        news_strategy_profile=cls._parse_news_strategy_profile(os.getenv("NEWS_STRATEGY_PROFILE", "short")),
        bias_threshold=parse_env_float(os.getenv("BIAS_THRESHOLD"), 5.0, field_name="BIAS_THRESHOLD", minimum=1.0),
        report_language=cls._parse_report_language(report_language_raw),
        wechat_webhook_url=(os.getenv("WECHAT_WEBHOOK_URL") or "").strip(),
        wechat_max_bytes=wechat_max_bytes,
        wechat_msg_type=wechat_msg_type_lower,
        database_url=(os.getenv("DATABASE_URL") or "").strip() or None,
        database_path=os.getenv("DATABASE_PATH", "./data/stock_analysis.db"),
        sqlite_wal_enabled=os.getenv("SQLITE_WAL_ENABLED", "true").lower() == "true",
        sqlite_busy_timeout_ms=parse_env_int(
            os.getenv("SQLITE_BUSY_TIMEOUT_MS"), 5000, field_name="SQLITE_BUSY_TIMEOUT_MS", minimum=0
        ),
        sqlite_write_retry_max=parse_env_int(
            os.getenv("SQLITE_WRITE_RETRY_MAX"), 3, field_name="SQLITE_WRITE_RETRY_MAX", minimum=0
        ),
        sqlite_write_retry_base_delay=parse_env_float(
            os.getenv("SQLITE_WRITE_RETRY_BASE_DELAY"), 0.1, field_name="SQLITE_WRITE_RETRY_BASE_DELAY", minimum=0.0
        ),
        save_context_snapshot=os.getenv("SAVE_CONTEXT_SNAPSHOT", "true").lower() == "true",
        log_dir=os.getenv("LOG_DIR", "./logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        debug=os.getenv("DEBUG", "false").lower() == "true",
        config_validate_mode=os.getenv("CONFIG_VALIDATE_MODE", "warn").lower(),
        http_proxy=os.getenv("HTTP_PROXY"),
        https_proxy=os.getenv("HTTPS_PROXY"),
        rsshub_base_url=(os.getenv("RSSHUB_BASE_URL") or "http://127.0.0.1:1200").rstrip("/"),
        webui_enabled=os.getenv("WEBUI_ENABLED", "false").lower() == "true",
        webui_host=os.getenv("WEBUI_HOST", "127.0.0.1"),
        webui_port=parse_env_int(os.getenv("WEBUI_PORT"), 8000, field_name="WEBUI_PORT", minimum=1, maximum=65535),
        bot_enabled=os.getenv("BOT_ENABLED", "true").lower() == "true",
        bot_command_prefix=os.getenv("BOT_COMMAND_PREFIX", "/"),
        bot_rate_limit_requests=parse_env_int(
            os.getenv("BOT_RATE_LIMIT_REQUESTS"), 10, field_name="BOT_RATE_LIMIT_REQUESTS", minimum=1
        ),
        bot_rate_limit_window=parse_env_int(
            os.getenv("BOT_RATE_LIMIT_WINDOW"), 60, field_name="BOT_RATE_LIMIT_WINDOW", minimum=1
        ),
        bot_admin_users=[u.strip() for u in os.getenv("BOT_ADMIN_USERS", "").split(",") if u.strip()],
        enable_realtime_quote=os.getenv("ENABLE_REALTIME_QUOTE", "true").lower() == "true",
        enable_realtime_technical_indicators=os.getenv("ENABLE_REALTIME_TECHNICAL_INDICATORS", "true").lower()
        == "true",
        enable_chip_distribution=os.getenv("ENABLE_CHIP_DISTRIBUTION", "true").lower() == "true",
        enable_eastmoney_patch=os.getenv("ENABLE_EASTMONEY_PATCH", "false").lower() == "true",
        realtime_cache_ttl=parse_env_int(
            os.getenv("REALTIME_CACHE_TTL"), 600, field_name="REALTIME_CACHE_TTL", minimum=0
        ),
        enable_fundamental_pipeline=os.getenv("ENABLE_FUNDAMENTAL_PIPELINE", "true").lower() == "true",
        fundamental_stage_timeout_seconds=parse_env_float(
            os.getenv("FUNDAMENTAL_STAGE_TIMEOUT_SECONDS"),
            fundamental_stage_timeout_seconds_default,
            field_name="FUNDAMENTAL_STAGE_TIMEOUT_SECONDS",
            minimum=0.0,
        ),
        fundamental_fetch_timeout_seconds=parse_env_float(
            os.getenv("FUNDAMENTAL_FETCH_TIMEOUT_SECONDS"),
            3.0,
            field_name="FUNDAMENTAL_FETCH_TIMEOUT_SECONDS",
            minimum=0.0,
        ),
        fundamental_retry_max=parse_env_int(
            os.getenv("FUNDAMENTAL_RETRY_MAX"), 1, field_name="FUNDAMENTAL_RETRY_MAX", minimum=0
        ),
        fundamental_cache_ttl_seconds=parse_env_int(
            os.getenv("FUNDAMENTAL_CACHE_TTL_SECONDS"),
            120,
            field_name="FUNDAMENTAL_CACHE_TTL_SECONDS",
            minimum=0,
        ),
        fundamental_cache_max_entries=parse_env_int(
            os.getenv("FUNDAMENTAL_CACHE_MAX_ENTRIES"),
            256,
            field_name="FUNDAMENTAL_CACHE_MAX_ENTRIES",
            minimum=1,
        ),
    )
