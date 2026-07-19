# -*- coding: utf-8 -*-
"""Tests for the notification channel supported by the product."""

from __future__ import annotations

from unittest import mock

from src.config import Config
from src.notification_sender import WechatSender, WECHAT_IMAGE_MAX_BYTES


def _config(**overrides) -> Config:
    overrides.setdefault("wechat_webhook_url", "")
    return Config(stock_list=[], **overrides)


def _response(status_code: int, json_body: dict | None = None):
    response = mock.MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    return response


def test_send_returns_false_when_webhook_is_not_configured() -> None:
    assert WechatSender(_config()).send_to_wechat("hello") is False


@mock.patch("src.notification_sender.wechat_sender.requests.post")
def test_send_success_uses_configured_webhook(mock_post: mock.MagicMock) -> None:
    mock_post.return_value = _response(200, {"errcode": 0})
    sender = WechatSender(
        _config(wechat_webhook_url="https://wechat.example/hook")
    )

    assert sender.send_to_wechat("hello") is True
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://wechat.example/hook"


@mock.patch("src.notification_sender.wechat_sender.requests.post")
def test_send_returns_false_for_wechat_error(mock_post: mock.MagicMock) -> None:
    mock_post.return_value = _response(200, {"errcode": 40013})
    sender = WechatSender(
        _config(wechat_webhook_url="https://wechat.example/hook")
    )

    assert sender.send_to_wechat("hello") is False


def test_markdown_payload() -> None:
    sender = WechatSender(
        _config(wechat_webhook_url="unused", wechat_msg_type="markdown")
    )

    assert sender._gen_wechat_payload("## title\nbody") == {
        "msgtype": "markdown",
        "markdown": {"content": "## title\nbody"},
    }


def test_text_payload() -> None:
    sender = WechatSender(
        _config(wechat_webhook_url="unused", wechat_msg_type="text")
    )

    assert sender._gen_wechat_payload("plain") == {
        "msgtype": "text",
        "text": {"content": "plain"},
    }


@mock.patch("src.notification_sender.wechat_sender.requests.post")
def test_image_over_limit_is_rejected_without_request(
    mock_post: mock.MagicMock,
) -> None:
    sender = WechatSender(
        _config(wechat_webhook_url="https://wechat.example/hook")
    )

    assert sender._send_wechat_image(b"x" * (WECHAT_IMAGE_MAX_BYTES + 1)) is False
    mock_post.assert_not_called()
