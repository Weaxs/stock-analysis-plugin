from argparse import Namespace
from types import SimpleNamespace

import pytest

from tools import value_data


def reply(payload):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)


def test_announcements_stock_identity_filters_dates_and_pagination(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: reply({"stockList": [{"code": "600519", "orgId": "gssz0600519"}]})
    )

    def post(url, **kwargs):
        calls.append(kwargs["data"].copy())
        page = kwargs["data"]["pageNum"]
        return reply(
            {
                "totalAnnouncement": 2,
                "announcements": [
                    {
                        "secCode": "600519",
                        "announcementTitle": "<em>业绩</em>快报",
                        "announcementTime": 1735689600000,
                        "adjunctUrl": f"finalpage/2025-01-01/{page}.PDF",
                    }
                ],
            }
        )

    monkeypatch.setattr("requests.post", post)
    result = value_data.announcements(
        Namespace(symbol="600519", category="express", start_date="2025-01-01", end_date="2025-01-02", top=2)
    )
    assert result["announcements"][0]["title"] == "业绩快报"
    assert result["announcements"][0]["date"] == "2025-01-01"
    assert result["announcements"][0]["pdf_url"].startswith("https://static.cninfo.com.cn/finalpage/")
    assert calls[0]["stock"] == "600519,gssz0600519"
    assert calls[0]["column"] == "szse"
    assert calls[0]["searchkey"] == "业绩快报"
    assert calls[0]["seDate"] == "2025-01-01~2025-01-02"


@pytest.mark.parametrize(
    "symbol,start,end,top",
    [
        ("0700.HK", "", "", 50),
        ("600519", "2025-02-30", "", 50),
        ("600519", "2026-01-01", "2025-01-01", 50),
        ("600519", "", "", 0),
    ],
)
def test_announcements_validation(symbol, start, end, top):
    with pytest.raises(ValueError):
        value_data.announcements(Namespace(symbol=symbol, category="all", start_date=start, end_date=end, top=top))


def test_announcements_paginates_bounded_results_and_removes_foreign_stock(monkeypatch):
    monkeypatch.setattr(
        "requests.get", lambda *a, **k: reply({"stockList": [{"code": "000001", "orgId": "gssz0000001"}]})
    )
    pages = []

    def post(url, **kwargs):
        page = kwargs["data"]["pageNum"]
        pages.append(page)
        rows = [
            {
                "secCode": "000001",
                "announcementTitle": "年度报告",
                "announcementTime": 1735689600000,
                "adjunctUrl": f"finalpage/2025-01-01/{page}-{i}.PDF",
            }
            for i in range(30)
        ]
        if page == 1:
            rows[0]["secCode"] = "600519"
        return reply({"totalAnnouncement": 60, "announcements": rows})

    monkeypatch.setattr("requests.post", post)
    result = value_data.announcements(
        Namespace(symbol="000001", category="all", start_date="2025-01-01", end_date="2025-01-02", top=31)
    )
    assert pages == [1, 2]
    assert len(result["announcements"]) == 31
    assert result["truncated"] is True
    assert all(row["category"] == "annual" for row in result["announcements"])
    assert not any(row["pdf_url"].endswith("1-0.PDF") for row in result["announcements"])
