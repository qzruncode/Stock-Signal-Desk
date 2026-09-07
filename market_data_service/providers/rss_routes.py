# -*- coding: utf-8 -*-
"""RSSHub route mapping table — static source definitions."""

from __future__ import annotations

from typing import Any

RSSHUB_ROUTES: dict[str, dict[str, Any]] = {
    "xueqiu_info": {
        "path": "/xueqiu/stock_info/{id}/{type}",
        "label": "雪球个股资讯",
        "group": "个股",
        "requires_stock": True,
        "requires_type": True,
        "type_options": [
            {"value": "announcement", "label": "公告"},
            {"value": "news", "label": "资讯"},
            {"value": "research", "label": "研报"},
        ],
        "default_type": "news",
    },
    "xueqiu_comments": {
        "path": "/xueqiu/stock_comments/{id}",
        "label": "雪球个股评论",
        "group": "个股",
        "requires_stock": True,
    },
    "wallstreetcn": {
        "path": "/wallstreetcn/news/{category}",
        "label": "华尔街见闻新闻",
        "group": "快讯",
        "requires_category": True,
        "category_options": [
            {"value": "global", "label": "最新"},
            {"value": "shares", "label": "股市"},
            {"value": "bonds", "label": "债市"},
            {"value": "commodities", "label": "商品"},
            {"value": "forex", "label": "外汇"},
        ],
        "default_category": "global",
    },
    "wallstreetcn_hot": {
        "path": "/wallstreetcn/hot",
        "label": "华尔街见闻热门",
        "group": "快讯",
    },
    "wallstreetcn_live": {
        "path": "/wallstreetcn/live",
        "label": "华尔街见闻实时快讯",
        "group": "快讯",
    },
    "jqka_realtime": {
        "path": "/10jqka/realtimenews/{category}",
        "label": "同花顺7×24快讯",
        "group": "快讯",
        "requires_category": False,
        "default_category": "",
    },
    "stcn_kx": {
        "path": "/stcn/article/list/kx",
        "label": "证券时报快讯",
        "group": "快讯",
    },
    "gelonghui_keyword": {
        "path": "/gelonghui/keyword/{keyword}",
        "label": "格隆汇关键词",
        "group": "个股",
        "requires_keyword": True,
    },
    "wkjyqh_research": {
        "path": "/wkjyqh/research",
        "label": "五矿期货研究",
        "group": "研报",
    },
    "wallstreetcn_calendar": {
        "path": "/wallstreetcn/calendar",
        "label": "华尔街见闻财经日历",
        "group": "宏观",
    },
    "cls": {
        "path": "/cls/{category}",
        "label": "财联社",
        "group": "快讯",
        "requires_category": True,
        "category_options": [
            {"value": "telegraph", "label": "电报快讯"},
            {"value": "depth", "label": "深度文章"},
            {"value": "hot", "label": "热门文章"},
        ],
        "default_category": "telegraph",
    },
    "sina_roll": {
        "path": "/sina/rollnews/{category}",
        "label": "新浪财经滚动",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "2509", "label": "全部"},
            {"value": "2517", "label": "股市"},
            {"value": "2516", "label": "财经"},
            {"value": "2518", "label": "美股"},
        ],
        "default_category": "2517",
    },
    "sina_finance": {
        "path": "/sina/finance/{category}",
        "label": "新浪财经频道",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "china", "label": "中国财经"},
            {"value": "rollnews", "label": "财经滚动"},
            {"value": "stock/usstock", "label": "美股资讯"},
        ],
        "default_category": "china",
    },
    "eastmoney_report": {
        "path": "/eastmoney/report/{category}",
        "label": "东方财富研报",
        "group": "研报",
        "requires_category": True,
        "category_options": [
            {"value": "stock", "label": "个股研报"},
            {"value": "industry", "label": "行业研报"},
            {"value": "strategyreport", "label": "策略研报"},
            {"value": "macresearch", "label": "宏观研报"},
        ],
        "default_category": "stock",
    },
    "eastmoney_search": {
        "path": "/eastmoney/search/{keyword}",
        "label": "东方财富搜索新闻",
        "group": "个股",
        "requires_keyword": True,
        "keyword_placeholder": "关键词/股票代码 (如 600519)",
    },
    "yicai": {
        "path": "/yicai/{category}",
        "label": "第一财经",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "latest", "label": "最新资讯"},
            {"value": "brief", "label": "快讯"},
            {"value": "headline", "label": "头条"},
            {"value": "news", "label": "新闻"},
            {"value": "vip", "label": "会员文章"},
            {"value": "video", "label": "视频"},
            {"value": "dt", "label": "读书"},
        ],
        "default_category": "latest",
    },
    "36kr": {
        "path": "/36kr/{category}",
        "label": "36氪",
        "group": "科技创投",
        "requires_category": True,
        "category_options": [
            {"value": "newsflashes", "label": "快讯"},
            {"value": "information/web_news", "label": "网页新闻"},
            {"value": "hot-list", "label": "热榜"},
        ],
        "default_category": "newsflashes",
    },
    "szse_disclosure": {
        "path": "/szse/disclosure/listed/notice/{query}",
        "label": "深交所公告",
        "group": "交易所/监管",
        "requires_stock": True,
        "stock_placeholder": "股票代码 (如 000001)",
    },
    "sse_inquire": {
        "path": "/sse/inquire",
        "label": "上交所监管问询",
        "group": "交易所/监管",
    },
    "sse_disclosure": {
        "path": "/sse/disclosure",
        "label": "上交所披露",
        "group": "交易所/监管",
    },
    "bloomberg_markets": {
        "path": "/bloomberg/markets",
        "label": "Bloomberg 市场资讯",
        "group": "海外",
    },
    "jrj": {
        "path": "/jrj/{category}",
        "label": "金融界资讯",
        "group": "宏观",
        "requires_category": True,
        "category_options": [
            {"value": "103", "label": "财经资讯"},
            {"value": "102", "label": "美股资讯"},
            {"value": "104", "label": "基金资讯"},
            {"value": "107", "label": "期货资讯"},
        ],
        "default_category": "103",
    },
    "chinamoney": {
        "path": "/chinamoney",
        "label": "中国外汇交易中心公告",
        "group": "监管/公告",
    },
    "ulapia": {
        "path": "/ulapia/reports/{category}",
        "label": "ulapia 研报",
        "group": "研报",
        "requires_category": True,
        "category_options": [
            {"value": "stock_research", "label": "个股研报"},
            {"value": "industry_research", "label": "行业研报"},
            {"value": "strategy_research", "label": "策略研报"},
            {"value": "macro_research", "label": "宏观研报"},
            {"value": "brokerage_news", "label": "券商晨报"},
        ],
        "default_category": "stock_research",
    },
}
