# -*- coding: utf-8 -*-
"""
===================================
API v1 Schemas 模块初始化
===================================

职责：
1. 导出所有 Pydantic 模型
"""

from api.v1.schemas.common import (
    RootResponse,
    HealthResponse,
    ErrorResponse,
    SuccessResponse,
)
from api.v1.schemas.history import (
    AnalysisReport,
    ReportMeta,
    ReportSummary,
    ReportStrategy,
    ReportDetails,
)
from api.v1.schemas.system_config import (
    SystemConfigFieldSchema,
    SystemConfigCategorySchema,
    SystemConfigSchemaResponse,
    SystemConfigItem,
    SystemConfigResponse,
    SystemConfigUpdateItem,
    UpdateSystemConfigRequest,
    UpdateSystemConfigResponse,
    ConfigValidationIssue,
    SystemConfigValidationErrorResponse,
    SystemConfigConflictResponse,
    TestModelConnectionRequest,
    TestModelConnectionResponse,
)

__all__ = [
    # common
    "RootResponse",
    "HealthResponse",
    "ErrorResponse",
    "SuccessResponse",
    # persisted analysis report projection
    "AnalysisReport",
    "ReportMeta",
    "ReportSummary",
    "ReportStrategy",
    "ReportDetails",
    # system config
    "SystemConfigFieldSchema",
    "SystemConfigCategorySchema",
    "SystemConfigSchemaResponse",
    "SystemConfigItem",
    "SystemConfigResponse",
    "SystemConfigUpdateItem",
    "UpdateSystemConfigRequest",
    "UpdateSystemConfigResponse",
    "ConfigValidationIssue",
    "SystemConfigValidationErrorResponse",
    "SystemConfigConflictResponse",
    "TestModelConnectionRequest",
    "TestModelConnectionResponse",
]
