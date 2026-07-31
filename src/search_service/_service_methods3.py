"""Method group 3 for SearchService."""

from src.search_service._service import (
    logging,
    re,
    threading,
    date,
    datetime,
    timedelta,
    timezone,
    parsedate_to_datetime,
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    is_us_index_code,
    _ss,
    NEWS_STRATEGY_WINDOWS,
    normalize_news_strategy_profile,
    resolve_news_window_days,
    BaseSearchProvider,
    SearchResponse,
    SearchResult,
    AnspireSearchProvider,
    BochaSearchProvider,
    BraveSearchProvider,
    MiniMaxSearchProvider,
    SearXNGSearchProvider,
    SerpAPISearchProvider,
    TavilySearchProvider,
    logger,
 )

class _SearchServiceMethods3:
    def search_comprehensive_intel(
        self, stock_code: str, stock_name: str, max_searches: int = 3
    ) -> Dict[str, SearchResponse]:
        """
        多维度情报搜索（同时使用多个引擎、多个维度）

        搜索维度：
        1. 最新消息 - 近期新闻动态
        2. 风险排查 - 减持、处罚、利空
        3. 业绩预期 - 年报预告、业绩快报

        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            max_searches: 最大搜索次数

        Returns:
            {维度名称: SearchResponse} 字典
        """
        results = {}
        search_count = 0

        is_foreign = self._is_foreign_stock(stock_code)
        is_index_etf = self.is_index_or_etf(stock_code, stock_name)

        if is_foreign:
            search_dimensions = [
                {
                    "name": "latest_news",
                    "query": f"{stock_name} {stock_code} latest news events",
                    "desc": "最新消息",
                    "tavily_topic": "news",
                    "strict_freshness": True,
                },
                {
                    "name": "market_analysis",
                    "query": f"{stock_name} analyst rating target price report",
                    "desc": "机构分析",
                    "tavily_topic": None,
                    "strict_freshness": False,
                },
                {
                    "name": "risk_check",
                    "query": (
                        f"{stock_name} {stock_code} index performance outlook tracking error"
                        if is_index_etf
                        else f"{stock_name} risk insider selling lawsuit litigation"
                    ),
                    "desc": "风险排查",
                    "tavily_topic": None if is_index_etf else "news",
                    "strict_freshness": not is_index_etf,
                },
                {
                    "name": "earnings",
                    "query": (
                        f"{stock_name} {stock_code} index performance composition outlook"
                        if is_index_etf
                        else f"{stock_name} earnings revenue profit growth forecast"
                    ),
                    "desc": "业绩预期",
                    "tavily_topic": None,
                    "strict_freshness": False,
                },
                {
                    "name": "industry",
                    "query": (
                        f"{stock_name} {stock_code} index sector allocation holdings"
                        if is_index_etf
                        else f"{stock_name} industry competitors market share outlook"
                    ),
                    "desc": "行业分析",
                    "tavily_topic": None,
                    "strict_freshness": False,
                },
            ]
        else:
            search_dimensions = [
                {
                    "name": "latest_news",
                    "query": f"{stock_name} {stock_code} 最新 新闻 重大 事件",
                    "desc": "最新消息",
                    "tavily_topic": "news",
                    "strict_freshness": True,
                },
                {
                    "name": "market_analysis",
                    "query": f"{stock_name} 研报 目标价 评级 深度分析",
                    "desc": "机构分析",
                    "tavily_topic": None,
                    "strict_freshness": False,
                },
                {
                    "name": "risk_check",
                    "query": (
                        f"{stock_name} 指数走势 跟踪误差 净值 表现"
                        if is_index_etf
                        else f"{stock_name} 减持 处罚 违规 诉讼 利空 风险"
                    ),
                    "desc": "风险排查",
                    "tavily_topic": None if is_index_etf else "news",
                    "strict_freshness": not is_index_etf,
                },
                {
                    "name": "announcements",
                    "query": (
                        f"{stock_name} {stock_code} 公告 指数调整 成分变化"
                        if is_index_etf
                        else f"{stock_name} {stock_code} 公司公告 重要公告 上交所 深交所 cninfo"
                    ),
                    "desc": "公司公告",
                    "tavily_topic": "news",
                    "strict_freshness": True,
                },
                {
                    "name": "earnings",
                    "query": (
                        f"{stock_name} 指数成分 净值 跟踪表现"
                        if is_index_etf
                        else f"{stock_name} 业绩预告 财报 营收 净利润 同比增长"
                    ),
                    "desc": "业绩预期",
                    "tavily_topic": None,
                    "strict_freshness": False,
                },
                {
                    "name": "industry",
                    "query": (
                        f"{stock_name} 指数成分股 行业配置 权重"
                        if is_index_etf
                        else f"{stock_name} 所在行业 竞争对手 市场份额 行业前景"
                    ),
                    "desc": "行业分析",
                    "tavily_topic": None,
                    "strict_freshness": False,
                },
            ]

        search_days = self._effective_news_window_days()
        target_per_dimension = 3
        provider_max_results = self._provider_request_size(target_per_dimension)

        logger.info(
            (
                "开始多维度情报搜索: %s(%s), 时间范围: 近%s天 "
                "(profile=%s, NEWS_MAX_AGE_DAYS=%s), 目标条数=%s, provider请求条数=%s"
            ),
            stock_name,
            stock_code,
            search_days,
            self.news_strategy_profile,
            self.news_max_age_days,
            target_per_dimension,
            provider_max_results,
        )

        # 轮流使用不同的搜索引擎
        provider_index = 0

        for dim in search_dimensions:
            if search_count >= max_searches:
                break

            # 选择搜索引擎（轮流使用）
            available_providers = [p for p in self._providers if p.is_available]
            if not available_providers:
                break

            provider = available_providers[provider_index % len(available_providers)]
            provider_index += 1

            logger.info(f"[情报搜索] {dim['desc']}: 使用 {provider.name}")

            if isinstance(provider, TavilySearchProvider) and dim.get("tavily_topic"):
                response = provider.search(
                    dim["query"],
                    max_results=provider_max_results,
                    days=search_days,
                    topic=dim["tavily_topic"],
                )
            else:
                response = provider.search(
                    dim["query"],
                    max_results=provider_max_results,
                    days=search_days,
                )
            if dim["strict_freshness"]:
                filtered_response = self._filter_news_response(
                    response,
                    search_days=search_days,
                    max_results=target_per_dimension,
                    log_scope=f"{stock_code}:{provider.name}:{dim['name']}",
                )
            else:
                filtered_response = self._normalize_and_limit_response(
                    response,
                    max_results=target_per_dimension,
                )
            results[dim["name"]] = filtered_response
            search_count += 1

            if response.success:
                logger.info(
                    "[情报搜索] %s: 原始=%s条, 过滤后=%s条",
                    dim["desc"],
                    len(response.results),
                    len(filtered_response.results),
                )
            else:
                logger.warning(f"[情报搜索] {dim['desc']}: 搜索失败 - {response.error_message}")

            # 短暂延迟避免请求过快
            _ss.time.sleep(0.5)

        return results
    def format_intel_report(self, intel_results: Dict[str, SearchResponse], stock_name: str) -> str:
        """
        格式化情报搜索结果为报告

        Args:
            intel_results: 多维度搜索结果
            stock_name: 股票名称

        Returns:
            格式化的情报报告文本
        """
        lines = [f"【{stock_name} 情报搜索结果】"]

        # 维度展示顺序
        display_order = ["latest_news", "announcements", "market_analysis", "risk_check", "earnings", "industry"]

        dim_labels = {
            "latest_news": "📰 最新消息",
            "announcements": "📋 公司公告",
            "market_analysis": "📈 机构分析",
            "risk_check": "⚠️ 风险排查",
            "earnings": "📊 业绩预期",
            "industry": "🏭 行业分析",
        }

        for dim_name in display_order:
            if dim_name not in intel_results:
                continue

            resp = intel_results[dim_name]

            # 获取维度描述
            dim_desc = dim_labels.get(dim_name, dim_name)

            lines.append(f"\n{dim_desc} (来源: {resp.provider}):")
            if resp.success and resp.results:
                # 增加显示条数
                for i, r in enumerate(resp.results[:4], 1):
                    date_str = f" [{r.published_date}]" if r.published_date else ""
                    lines.append(f"  {i}. {r.title}{date_str}")
                    # 如果摘要太短，可能信息量不足
                    snippet = r.snippet[:150] if len(r.snippet) > 20 else r.snippet
                    lines.append(f"     {snippet}...")
            else:
                lines.append("  未找到相关信息")

        return "\n".join(lines)
    def batch_search(
        self, stocks: List[Dict[str, str]], max_results_per_stock: int = 3, delay_between: float = 1.0
    ) -> Dict[str, SearchResponse]:
        """
        Batch search news for multiple stocks.

        Args:
            stocks: List of stocks
            max_results_per_stock: Max results per stock
            delay_between: Delay between searches (seconds)

        Returns:
            Dict of results
        """
        results = {}

        for i, stock in enumerate(stocks):
            if i > 0:
                _ss.time.sleep(delay_between)

            code = stock.get("code", "")
            name = stock.get("name", "")

            response = self.search_stock_news(code, name, max_results_per_stock)
            results[code] = response

        return results
    def search_stock_price_fallback(
        self, stock_code: str, stock_name: str, max_attempts: int = 3, max_results: int = 5
    ) -> SearchResponse:
        """
        Enhance search when data sources fail.

        When all data sources (efinance, akshare, tushare, baostock, etc.) fail to get
        stock data, use search engines to find stock trends and price info as supplemental data for AI analysis.

        Strategy:
        1. Search using multiple keyword templates
        2. Try all available search engines for each keyword
        3. Aggregate and deduplicate results

        Args:
            stock_code: Stock Code
            stock_name: Stock Name
            max_attempts: Max search attempts (using different keywords)
            max_results: Max results to return

        Returns:
            SearchResponse object with aggregated results
        """

        if not self.is_available:
            return SearchResponse(
                query=f"{stock_name} 股价走势",
                results=[],
                provider="None",
                success=False,
                error_message="未配置搜索能力",
            )

        logger.info(f"[增强搜索] 数据源失败，启动增强搜索: {stock_name}({stock_code})")

        all_results = []
        seen_urls = set()
        successful_providers = []

        # 使用多个关键词模板搜索
        is_foreign = self._is_foreign_stock(stock_code)
        keywords = self.ENHANCED_SEARCH_KEYWORDS_EN if is_foreign else self.ENHANCED_SEARCH_KEYWORDS
        for i, keyword_template in enumerate(keywords[:max_attempts]):
            query = keyword_template.format(name=stock_name, code=stock_code)

            logger.info(f"[增强搜索] 第 {i+1}/{max_attempts} 次搜索: {query}")

            # 依次尝试各个搜索引擎
            for provider in self._providers:
                if not provider.is_available:
                    continue

                try:
                    response = provider.search(query, max_results=3)

                    if response.success and response.results:
                        # 去重并添加结果
                        for result in response.results:
                            if result.url not in seen_urls:
                                seen_urls.add(result.url)
                                all_results.append(result)

                        if provider.name not in successful_providers:
                            successful_providers.append(provider.name)

                        logger.info(f"[增强搜索] {provider.name} 返回 {len(response.results)} 条结果")
                        break  # 成功后跳到下一个关键词
                    else:
                        logger.debug(f"[增强搜索] {provider.name} 无结果或失败")

                except Exception as e:
                    logger.warning(f"[增强搜索] {provider.name} 搜索异常: {e}")
                    continue

            # 短暂延迟避免请求过快
            if i < max_attempts - 1:
                _ss.time.sleep(0.5)

        # 汇总结果
        if all_results:
            # 截取前 max_results 条
            final_results = all_results[:max_results]
            provider_str = ", ".join(successful_providers) if successful_providers else "None"

            logger.info(f"[增强搜索] 完成，共获取 {len(final_results)} 条结果（来源: {provider_str}）")

            return SearchResponse(
                query=f"{stock_name}({stock_code}) 股价走势",
                results=final_results,
                provider=provider_str,
                success=True,
            )
        else:
            logger.warning(f"[增强搜索] 所有搜索均未返回结果")
            return SearchResponse(
                query=f"{stock_name}({stock_code}) 股价走势",
                results=[],
                provider="None",
                success=False,
                error_message="增强搜索未找到相关信息",
            )
    def search_stock_with_enhanced_fallback(
        self,
        stock_code: str,
        stock_name: str,
        include_news: bool = True,
        include_price: bool = False,
        max_results: int = 5,
    ) -> Dict[str, SearchResponse]:
        """
        综合搜索接口（支持新闻和股价信息）

        当 include_price=True 时，会同时搜索新闻和股价信息。
        主要用于数据源完全失败时的兜底方案。

        Args:
            stock_code: 股票代码
            stock_name: 股票名称
            include_news: 是否搜索新闻
            include_price: 是否搜索股价/走势信息
            max_results: 每类搜索的最大结果数

        Returns:
            {'news': SearchResponse, 'price': SearchResponse} 字典
        """
        results = {}

        if include_news:
            results["news"] = self.search_stock_news(stock_code, stock_name, max_results=max_results)

        if include_price:
            results["price"] = self.search_stock_price_fallback(
                stock_code, stock_name, max_attempts=3, max_results=max_results
            )

        return results
    def format_price_search_context(self, response: SearchResponse) -> str:
        """
        将股价搜索结果格式化为 AI 分析上下文

        Args:
            response: 搜索响应对象

        Returns:
            格式化的文本，可直接用于 AI 分析
        """
        if not response.success or not response.results:
            return "【股价走势搜索】未找到相关信息，请以其他渠道数据为准。"

        lines = [
            f"【股价走势搜索结果】（来源: {response.provider}）",
            "⚠️ 注意：以下信息来自网络搜索，仅供参考，可能存在延迟或不准确。",
            "",
        ]

        for i, result in enumerate(response.results, 1):
            date_str = f" [{result.published_date}]" if result.published_date else ""
            lines.append(f"{i}. 【{result.source}】{result.title}{date_str}")
            lines.append(f"   {result.snippet[:200]}...")
            lines.append("")

        return "\n".join(lines)
