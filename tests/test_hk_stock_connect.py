import json
from io import BytesIO

import pandas as pd
import pytest
import requests

from tools import value_data


@pytest.fixture
def exchange_lists(monkeypatch):
    sse = {
        "actionErrors": [],
        "pageHelp": {"total": 2},
        "result": [{"SECURITY_CODE": "00700"}, {"SECURITY_CODE": "09988"}],
    }
    szse = ["700", "09999"]
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        if "query.sse.com.cn" in url:
            response._content = json.dumps(sse).encode()
        elif "www.szse.cn" in url:
            content = BytesIO()
            pd.DataFrame({"证券代码": szse}).to_excel(content, index=False)
            response._content = content.getvalue()
        else:
            raise AssertionError(f"unexpected Stock Connect source: {url}")
        return response

    monkeypatch.setattr(requests, "get", fetch)
    return sse, szse, calls


def test_stock_connect_combines_official_exchange_lists_and_caches(exchange_lists):
    _, _, calls = exchange_lists

    assert value_data.hk_stock_connect() == {"00700", "09988", "09999"}
    assert value_data.hk_stock_connect() == {"00700", "09988", "09999"}
    assert len(calls) == 2


@pytest.mark.parametrize("incomplete_exchange", ["SSE", "SZSE"])
def test_stock_connect_rejects_incomplete_exchange_lists(exchange_lists, incomplete_exchange):
    sse, szse, _ = exchange_lists
    if incomplete_exchange == "SSE":
        sse["pageHelp"]["total"] = 3
    else:
        szse.clear()

    with pytest.raises(ValueError, match="incomplete"):
        value_data.hk_stock_connect()
