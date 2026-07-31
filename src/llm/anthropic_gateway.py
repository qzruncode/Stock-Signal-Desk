# -*- coding: utf-8 -*-
"""Anthropic 网关单源 LLM 接入 helper。

全后端统一的 LLM 调用入口：仅读 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
`ANTHROPIC_MODEL` 三个环境变量（由前端「设置 - 模型设置」页写入），三者任一缺失
即抛 :class:`AnthropicGatewayConfigError`，不回落到任何多供应商来源。

底层仍用 `litellm`（`custom_llm_provider="anthropic"`）发起 HTTP 调用，因此本模块
只收敛「配置解析 + kwargs 组装」，不替换 litellm 库本身。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict


class AnthropicGatewayConfigError(RuntimeError):
    """ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL 任一缺失时抛出。

    兼容旧的 ``AgentModelConfigError`` 语义（chat.py 历史抛出此错误），报错文案
    与前端「设置 - 模型设置」页一一对应。
    """

    # 报错文案前缀，与 chat.py 原 AgentModelConfigError 保持一致，便于上游捕获/展示
    MESSAGE_PREFIX = "AI 助手模型未配置完整，请前往「设置 - 模型设置」补全："


# 终端样式片段（ANSI 转义 / 尾部 SGR 残片）——用户从终端复制模型名时可能粘贴进来，
# 传给 litellm/网关前必须清洗（否则网关 400 或模型名不匹配）。
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TRAILING_SGR_FRAGMENT_RE = re.compile(r"(?:\[(?:0|1|2|3|4|5|7|9)(?:;\d+)*m\])+$")

# 模型名上下文窗口后缀：用户在 Setting 页填的模型名可能带 [1m] 表示 1M 上下文窗口，
# 无后缀则默认 200k。后缀仅用于本地窗口判定，传给 litellm/网关前必须剥离（否则网关 400）。
_CONTEXT_WINDOW_SUFFIX_RE = re.compile(r"\s*\[\s*1m\s*\]\s*$", re.IGNORECASE)
_DEFAULT_CONTEXT_WINDOW = 200_000
_1M_CONTEXT_WINDOW = 1_000_000


def _clean_model_name(model_name: str) -> str:
    """Remove terminal style fragments that can be pasted into the settings field."""
    cleaned = _ANSI_ESCAPE_RE.sub("", model_name).strip()
    return _TRAILING_SGR_FRAGMENT_RE.sub("", cleaned).strip()


def _parse_model_context_window(model_name: str) -> tuple[str, int]:
    """从模型名解析上下文窗口：带 [1m] 后缀 → 1,000,000，否则 200,000。

    返回 (剥离后缀的干净模型名, 窗口 token 数)。后缀大小写不敏感、允许内部空格。
    """
    if _CONTEXT_WINDOW_SUFFIX_RE.search(model_name):
        clean = _CONTEXT_WINDOW_SUFFIX_RE.sub("", model_name).strip()
        return clean, _1M_CONTEXT_WINDOW
    return model_name, _DEFAULT_CONTEXT_WINDOW


def resolve_anthropic_gateway_config() -> Dict[str, Any]:
    """Resolve the Anthropic gateway model config from env (ANTHROPIC_*).

    仅使用「设置 - 模型设置」保存的接入地址、鉴权令牌和主模型；三者任一
    缺失即报错，不回落到 AGENT_LITELLM_MODEL / litellm_model 等其他来源。
    """
    base_url = (os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    auth_token = (os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()
    raw_model = os.getenv("ANTHROPIC_MODEL") or ""

    missing = []
    if not base_url:
        missing.append("接入地址(ANTHROPIC_BASE_URL)")
    if not auth_token:
        missing.append("鉴权令牌(ANTHROPIC_AUTH_TOKEN)")
    if not raw_model.strip():
        missing.append("主模型(ANTHROPIC_MODEL)")
    if missing:
        raise AnthropicGatewayConfigError(AnthropicGatewayConfigError.MESSAGE_PREFIX + "、".join(missing))

    # 先解析 [1m] 窗口后缀（必须先于 _clean_model_name：SGR 正则会误吃 [1m] 字面后缀），
    # 再清洗 ANSI 转义。后缀仅用于本地窗口判定，传给 litellm/网关前剥离。
    stripped_model, context_window = _parse_model_context_window(raw_model.strip())
    model_name = _clean_model_name(stripped_model)

    return {
        "model": model_name,
        "custom_llm_provider": "anthropic",
        "api_key": auth_token,
        "api_base": base_url,
        "extra_headers": {"authorization": f"Bearer {auth_token}"},
        "context_window": context_window,
        # Production Agent turns use a small forced-schema model call for
        # standard-task planning.  Test/local callers that construct a
        # minimal config explicitly opt in, so existing low-level loop tests
        # remain isolated from this extra orchestration stage.
    }


def build_litellm_kwargs(llm_cfg: Dict[str, Any], *, stream: bool, **extra: Any) -> Dict[str, Any]:
    """Assemble litellm kwargs from the gateway config.

    合并基础鉴权字段（model/api_key/api_base/custom_llm_provider/extra_headers，
    缺省不写）与调用方额外参数（messages/tools/tool_choice 等）。stream 由调用方
    显式指定，避免误传。
    """
    kwargs: Dict[str, Any] = {
        "model": llm_cfg["model"],
        "stream": stream,
    }
    if llm_cfg.get("api_key"):
        kwargs["api_key"] = llm_cfg["api_key"]
    if llm_cfg.get("api_base"):
        kwargs["api_base"] = llm_cfg["api_base"]
    if llm_cfg.get("custom_llm_provider"):
        kwargs["custom_llm_provider"] = llm_cfg["custom_llm_provider"]
    if llm_cfg.get("extra_headers"):
        kwargs["extra_headers"] = llm_cfg["extra_headers"]
    kwargs.update(extra)
    return kwargs


def completion_gateway(**extra: Any) -> Any:
    """Sync 入口：解析网关配置 → 组装 kwargs → ``litellm.completion``。

    调用方通过 ``messages``/``tools``/``max_tokens`` 等 extra 传业务参数，
    ``stream`` 默认 False（可由 extra 覆盖）。
    """
    import litellm  # 延迟导入，避免模块加载期强依赖

    llm_cfg = resolve_anthropic_gateway_config()
    stream = bool(extra.pop("stream", False))
    kwargs = build_litellm_kwargs(llm_cfg, stream=stream, **extra)
    return litellm.completion(**kwargs)


async def acompletion_gateway(**extra: Any) -> Any:
    """Async 入口：解析网关配置 → 组装 kwargs → ``await litellm.acompletion``。

    调用方通过 ``messages``/``tools``/``max_tokens`` 等 extra 传业务参数，
    ``stream`` 默认 True（与 AI 助手流式回答一致，可由 extra 覆盖）。
    """
    import litellm  # 延迟导入，避免模块加载期强依赖

    llm_cfg = resolve_anthropic_gateway_config()
    stream = bool(extra.pop("stream", True))
    kwargs = build_litellm_kwargs(llm_cfg, stream=stream, **extra)
    return await litellm.acompletion(**kwargs)
