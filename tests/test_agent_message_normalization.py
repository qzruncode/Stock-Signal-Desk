from src.agent.message_normalization import latest_user_text, message_content_text


def test_message_content_text_supports_ai_sdk_and_provider_blocks() -> None:
    assert message_content_text(
        [
            {"type": "reasoning", "text": "hidden"},
            {"type": "text", "text": "第一段"},
            {"type": "input_text", "text": "第二段"},
            {"text": {"value": "第三段"}},
        ]
    ) == "第一段\n第二段\n第三段"


def test_latest_user_text_uses_newest_mixed_shape_turn() -> None:
    messages = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "最新问题"},
                {"type": "file", "name": "ignored.txt"},
            ],
        },
    ]

    assert latest_user_text(messages) == "最新问题"


def test_latest_user_text_skips_empty_newer_user_blocks() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "有效问题"}]},
        {"role": "user", "content": [{"type": "image", "url": "image.png"}]},
    ]

    assert latest_user_text(messages) == "有效问题"
