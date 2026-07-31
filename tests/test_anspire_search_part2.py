# -*- coding: utf-8 -*-
"""
Anspire Search 搜索引擎测试套件

测试覆盖范围:
1. 配置加载测试 - 验证 anspire_api_keys 是否正确从环境变量加载
2. 服务初始化测试 - 验证 SearchService 是否正确初始化 AnspireSearchProvider
3. API 调用测试 - 实际调用 Anspire API 验证返回结果
4. 故障转移测试 - 验证无效 Key 时的错误处理和降级机制
5. 搜索功能测试 - 测试股票新闻搜索和通用搜索功能

运行方式:
```bash
# Windows PowerShell
$env:ANSPIRE_API_KEYS="your_test_api_key"
python -m pytest tests/test_anspire_search.py -v

# Linux/Mac
export ANSPIRE_API_KEYS="your_test_api_key"
python -m pytest tests/test_anspire_search.py -v
```
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

load_dotenv()

# 添加项目根目录到 Python 路径，解决模块导入问题
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.config import Config, get_config
from src.search_service import (
    AnspireSearchProvider,
    SearchService,
    get_search_service,
    reset_search_service,
)
from src.search_service._urls import extract_domain



"""Focused test slice 2; shared fixtures remain local to this slice."""

class _FakeResponse:
    """模拟 HTTP 响应对象"""

    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        return self._json_data

def run_manual_test():
    """手动测试函数（用于快速验证）"""
    import logging
    from src.config import get_config

    # 配置日志
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

    print("=" * 60)
    print("Anspire Search 快速测试")
    print("=" * 60)

    # 检查配置
    config = get_config()
    if not config.anspire_api_keys:
        print("\n❌ 未检测到 Anspire API Keys")
        print("请设置环境变量：")
        print('  Windows PowerShell: $env:ANSPIRE_API_KEYS="your_api_key"')
        print('  Linux/Mac: export ANSPIRE_API_KEYS="your_api_key"')
        return False

    print(f"\n✅ 已配置 {len(config.anspire_api_keys)} 个 Anspire API Key")

    # 创建服务
    service = SearchService(
        anspire_keys=config.anspire_api_keys,
        bocha_keys=config.bocha_api_keys,
        tavily_keys=config.tavily_keys,
        searxng_public_instances_enabled=False,
        news_max_age_days=3,
        news_strategy_profile="short",
    )

    # 验证 Provider
    anspire_provider = service._providers[0] if service._providers else None
    if not anspire_provider or not isinstance(anspire_provider, AnspireSearchProvider):
        print("\n❌ Anspire Provider 未正确初始化")
        return False

    print(f"✅ Anspire Provider 初始化成功")
    print(f"   Provider 名称：{anspire_provider.name}")
    if hasattr(anspire_provider, "api_keys"):
        print(f"   API Keys 数量：{len(anspire_provider.api_keys)}")
    elif hasattr(anspire_provider, "_api_keys"):
        print(f"   API Keys 数量：{len(anspire_provider._api_keys)}")

    # 执行测试搜索
    print("\n" + "=" * 60)
    print("执行测试搜索：贵州茅台 (600519)")
    print("=" * 60)

    response = service.search_stock_news("600519", "贵州茅台", max_results=3)

    print(f"\n搜索结果:")
    print(f"  状态：{'✅ 成功' if response.success else '❌ 失败'}")
    print(f"  搜索引擎：{response.provider}")
    print(f"  结果数量：{len(response.results)}")
    print(f"  耗时：{response.search_time:.2f}s")

    if response.error_message:
        print(f"  错误信息：{response.error_message}")

    if response.results:
        print(f"\n前 {min(2, len(response.results))} 条结果预览:")
        for i, result in enumerate(response.results[:2], 1):
            print(f"\n  [{i}] {result.title}")
            print(f"      来源：{result.source}")
            print(f"      URL: {result.url}")
            if result.snippet:
                snippet_preview = result.snippet[:100] + "..." if len(result.snippet) > 100 else result.snippet
                print(f"      摘要：{snippet_preview}")

    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)

    return response.success
class TestAnspireIntegration(unittest.TestCase):
    """Anspire 集成测试（需要真实 API Key）"""

    @classmethod
    def setUpClass(cls):
        """Check if API Key is configured."""
        cls.api_keys = [k.strip() for k in os.getenv("ANSPIRE_API_KEYS", "").split(",") if k.strip()]
        cls.has_api_key = len(cls.api_keys) > 0

        if cls.has_api_key:
            reset_search_service()
            cls.service = get_search_service()

    @unittest.skipIf(not os.environ.get("ANSPIRE_API_KEYS"), "未设置 ANSPIRE_API_KEYS 环境变量，跳过集成测试")
    @pytest.mark.network
    def test_real_api_call_stock_news(self):
        """真实 API 调用测试 - 股票新闻搜索"""
        # 确保服务已重置
        reset_search_service()
        service = get_search_service()

        # 验证 Anspire 已配置
        anspire_provider = None
        for provider in service._providers:
            if isinstance(provider, AnspireSearchProvider):
                anspire_provider = provider
                break

        if not anspire_provider:
            self.skipTest("Anspire Provider 未初始化")

        # 测试 A 股搜索
        response = service.search_stock_news("600519", "贵州茅台", max_results=3)

        print(f"\n=== Anspire 真实 API 测试结果 ===")
        print(f"搜索状态：{'成功' if response.success else '失败'}")
        print(f"搜索引擎：{response.provider}")
        print(f"结果数量：{len(response.results)}")
        print(f"耗时：{response.search_time:.2f}s")

        # 基本验证
        self.assertTrue(response.success, f"搜索失败：{response.error_message}")
        self.assertEqual(response.provider, "Anspire")
        self.assertGreater(len(response.results), 0, "应至少返回一条结果")

        # 验证结果格式
        for result in response.results:
            self.assertIsNotNone(result.title)
            self.assertIsNotNone(result.url)
            # snippet 可能为空，视具体实现而定
            # self.assertIsNotNone(result.snippet)

    @unittest.skipIf(not os.environ.get("ANSPIRE_API_KEYS"), "未设置 ANSPIRE_API_KEYS 环境变量，跳过集成测试")
    @pytest.mark.network
    def test_real_api_call_general_search(self):
        """真实 API 调用测试 - 通用搜索"""
        reset_search_service()
        service = get_search_service()

        anspire_provider = None
        for provider in service._providers:
            if isinstance(provider, AnspireSearchProvider):
                anspire_provider = provider
                break

        if not anspire_provider:
            self.skipTest("Anspire Provider 未初始化")

        # 测试通用搜索
        response = anspire_provider.search("人工智能最新发展", max_results=5, days=7)

        print(f"\n=== Anspire 通用搜索结果 ===")
        print(f"搜索状态：{'成功' if response.success else '失败'}")
        print(f"结果数量：{len(response.results)}")

        self.assertTrue(response.success)
        self.assertGreater(len(response.results), 0)
