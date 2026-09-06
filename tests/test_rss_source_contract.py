from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from src.agent.langgraph_runtime.agent_tools import build_langchain_tools
from src.tools.registry import ToolRegistry
from src.tools.source_operations import RSS_SOURCE_CATALOG


@pytest.fixture(scope="module")
def registry():
    return ToolRegistry()


@pytest.mark.parametrize("source", RSS_SOURCE_CATALOG, ids=lambda source: source["id"])
def test_every_rss_source_rejects_undeclared_parameters(registry, source):
    params = {
        item["name"]: (item["enum"][0] if item["enum"] else "test")
        for item in source["parameters"] if item["required"]
    }
    arguments = {"source_id": source["id"], "source_params": params}
    assert registry.validate_model_arguments("read_rss_source", arguments)["source_id"] == source["id"]
    with pytest.raises(ValidationError, match="extra_forbidden"):
        registry.validate_model_arguments("read_rss_source", {
            **arguments, "source_params": {**params, "undeclared_parameter": "test"},
        })


@pytest.mark.parametrize("arguments, error_field", [
    ({"source_id": "cls_subject", "source_params": {"subject": "未来产业"}}, "subject"),
    ({"source_id": "cls_subject", "source_params": {"category": "global"}}, "category"),
    ({"source_id": "futunn_topic"}, "id"),
    ({"source_id": "futunn_topic", "source_params": {"id": "  "}}, "id"),
    ({"source_id": "eastmoney_reports", "source_params": {"category": "unknown"}}, "category"),
    ({"source_id": "cls_subject", "source_params": {"id": {"nested": "value"}}}, "id"),
    ({"source_id": "cls_subject", "source_params": {"id": False}}, "id"),
    ({"source_id": "cls_subject", "source_params": []}, "source_params"),
])
def test_invalid_rss_arguments_are_rejected_before_external_io(registry, arguments, error_field):
    with patch("src.tools.source_operations.read_rss_feed") as read_feed:
        with pytest.raises(ValidationError, match=error_field):
            registry.execute("read_rss_source", arguments)
    read_feed.assert_not_called()


@pytest.mark.parametrize("source_id, params, expected", [
    ("cls_subject", {"id": 101}, {"id": "101"}),
    ("cls_subject", {"id": " 101 "}, {"id": "101"}),
    ("cls_subject", {"id": None}, {}),
    ("cls_hot_articles", {}, {}),
    ("wallstreetcn_live", {"score": "2"}, {"category": "global", "score": "2"}),
])
def test_rss_path_normalization_preserves_existing_api_contract(registry, source_id, params, expected):
    normalized = registry.validate_model_arguments("read_rss_source", {
        "source_id": source_id, "source_params": params,
    })
    assert normalized["source_params"] == expected


def test_provider_bound_schema_retains_rss_parameter_constraints(registry):
    tool = next(tool for tool in build_langchain_tools(registry) if tool.name == "read_rss_source")
    schema = convert_to_openai_tool(tool)["function"]["parameters"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"source_id": "cls_subject", "source_params": {"id": "101"}})
    assert not validator.is_valid({"source_id": "cls_subject", "source_params": {"subject": "未来产业"}})
    variants = schema["properties"]["source_params"]["anyOf"]
    assert len(variants) == len(RSS_SOURCE_CATALOG)
    assert all(variant["additionalProperties"] is False for variant in variants)


def test_source_directory_only_references_model_callable_catalog_operation(registry):
    for source in RSS_SOURCE_CATALOG:
        if "list_rss_" in source["purpose"]:
            assert "list_rss_source_catalog(" in source["purpose"]
    assert registry.get_tool("list_rss_source_catalog") is not None
