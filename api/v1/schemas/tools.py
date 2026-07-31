# -*- coding: utf-8 -*-
"""
===================================
Agent 工具调试相关模型
===================================

职责：
1. 定义工具列表响应模型
2. 定义工具测试请求/响应模型
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ToolParameterSchema(BaseModel):
    """工具参数定义"""

    name: str = Field(..., description="参数名")
    type: str = Field(..., description="参数类型 (string|number|integer|boolean|array|object)")
    description: str = Field(..., description="参数描述")
    required: bool = Field(True, description="是否必填")
    enum: Optional[List[str]] = Field(None, description="可选值列表")
    default: Any = Field(None, description="默认值")


class ToolInfo(BaseModel):
    """单个工具信息"""

    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    parameters: List[ToolParameterSchema] = Field(default_factory=list, description="参数列表")
    category: str = Field(..., description="分类 (data|analysis|search|market)")
    display_name: str = Field(..., description="中文显示名称")
    healthy: bool = Field(True, description="工具是否可用")


class CategoryGroup(BaseModel):
    """按分类分组的工具列表"""

    category: str = Field(..., description="分类标识")
    display_name: str = Field(..., description="分类中文名")
    tools: List[ToolInfo] = Field(default_factory=list, description="该分类下的工具列表")


class ToolsListResponse(BaseModel):
    """工具列表响应"""

    groups: List[CategoryGroup] = Field(default_factory=list, description="按分类分组的工具列表")
    total: int = Field(0, description="工具总数")


class ToolTestRequest(BaseModel):
    """工具测试请求"""

    tool_name: str = Field(..., min_length=1, description="要测试的工具名称")
    stock_code: str = Field(..., min_length=1, description="股票代码，如 600519")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="额外参数")
    source: Optional[str] = Field(None, description="强制指定数据源。可选: em, sina, tencent")


class ToolTestResponse(BaseModel):
    """工具测试响应"""

    success: bool = Field(..., description="执行是否成功")
    tool_name: str = Field(..., description="工具名称")
    result: Optional[Any] = Field(None, description="原始返回结果 (JSON)")
    source: Optional[str] = Field(None, description="数据来源")
    duration_ms: Optional[float] = Field(None, description="执行耗时（毫秒）")
    error: Optional[str] = Field(None, description="错误信息")
