from __future__ import annotations

from unittest.mock import Mock

import pandas as pd
import pytest

from market_data_service import akshare_isolation


def test_mini_racer_akshare_call_uses_one_shot_worker_and_restores_dataframe(
    monkeypatch,
):
    execute = Mock(
        return_value={
            "__kind__": "dataframe",
            "columns": ["代码", "上市日期"],
            "records": [{"代码": "600000", "上市日期": "1999-11-10"}],
        }
    )
    monkeypatch.setattr(akshare_isolation, "execute_process_isolated", execute)

    result = akshare_isolation.call_akshare_isolated(
        "stock_profile_cninfo", symbol="600000"
    )

    assert isinstance(result, pd.DataFrame)
    assert result.to_dict(orient="records") == [
        {"代码": "600000", "上市日期": "1999-11-10"}
    ]
    command, request = execute.call_args.args
    assert command[-3:] == ["-m", "market_data_service.akshare_isolation", "--worker"]
    assert request == {
        "name": "stock_profile_cninfo",
        "args": [],
        "kwargs": {"symbol": "600000"},
    }
    assert execute.call_args.kwargs["result_prefix"] == akshare_isolation.RESULT_PREFIX


def test_mini_racer_akshare_call_rejects_unregistered_function():
    with pytest.raises(ValueError, match="未登记"):
        akshare_isolation.call_akshare_isolated("stock_zh_a_hist")
