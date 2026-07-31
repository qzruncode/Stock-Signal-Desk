# -*- coding: utf-8 -*-
"""Financial statements: quarter detection + source function re-exports.

``_pick_quarters`` is shared by all three source modules.
The actual data-fetch logic lives in sibling sub-modules.
"""

from __future__ import annotations


def _pick_quarters(df, periods: int):
    """Pick the last N quarters of data from the DataFrame.

    Different sources use different date column names:
    - EM: REPORT_DATE
    - THS: 报告期
    - Sina: 报告日

    We auto-detect and take the last ``periods`` rows.
    """
    if df is None or df.empty:
        return df
    for col in ["REPORT_DATE", "报告期", "报告日"]:
        if col in df.columns:
            df = df.sort_values(col)
            break
    return df.tail(periods)


# Re-export source functions for backward compatibility
from api.v1.endpoints.financials._em_statements import (  # noqa: E402, F401
    _fetch_balance_sheet,
    _fetch_income_statement,
    _fetch_cashflow,
    _fetch_from_em,
)
from api.v1.endpoints.financials._ths_statements import (  # noqa: E402, F401
    _fetch_from_ths_triple,
)
from api.v1.endpoints.financials._sina_statements import (  # noqa: E402, F401
    _fetch_from_sina_full,
)
from api.v1.endpoints.financials._normalize import (  # noqa: E402, F401
    _normalize_balance_debt_fields,
)
