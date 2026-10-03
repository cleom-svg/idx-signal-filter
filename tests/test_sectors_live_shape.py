"""Replays the response shapes captured by scripts/verify_api.py on 23 Sep 2026.

If Sectors changes its API, update these fixtures from a fresh verify run.
"""
import sectors


class FakeResp:
    def __init__(self, payload, code=200):
        self._p, self.status_code, self.headers = payload, code, {}
        self.text = str(payload)

    def json(self):
        return self._p


def client_with(responses, calls):
    c = sectors.SectorsClient(api_key="dummy")
    it = iter(responses)

    def fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        return next(it)

    c.session.get = fake_get
    return c


def page(rows, offset, total, limit):
    nxt = offset + len(rows)
    return {
        "results": rows,
        "pagination": {
            "total_count": total, "showing": len(rows), "limit": limit,
            "offset": offset, "has_next": nxt < total,
            "has_previous": offset > 0,
            "next_offset": nxt if nxt < total else None,
            "previous_offset": None,
        },
    }


def test_offset_pagination_walks_all_pages_and_never_sends_page():
    row = {"symbol": "AADI.JK", "date": "2026-09-23", "close": 11275}
    calls = []
    c = client_with(
        [FakeResp(page([row] * 100, 0, 250, 100)),
         FakeResp(page([row] * 100, 100, 250, 100)),
         FakeResp(page([row] * 50, 200, 250, 100))],
        calls,
    )
    rows = list(c.get_paginated("/close/", {"date": "2026-09-23"}))
    assert len(rows) == 250
    assert [p["offset"] for p in calls] == [0, 100, 200]
    assert all("page" not in p for p in calls)  # page= returns 400 live


def test_rejected_limit_falls_back_to_server_default():
    row = {"title": "x"}
    calls = []
    c = client_with(
        [FakeResp({"error": "bad limit"}, 400),
         FakeResp(page([row] * 20, 0, 20, 20))],
        calls,
    )
    rows = list(c.get_paginated("/news/", {"extension": "idx"}))
    assert len(rows) == 20
    assert "limit" in calls[0] and "limit" not in calls[1]


def test_bare_list_daily_response():
    raw = [{"symbol": "BBCA.JK", "date": "2026-09-18", "close": 6300,
            "volume": 176753700, "market_cap": 768866486850000}]
    c = client_with([FakeResp(raw)], [])
    assert c.daily_symbol("BBCA", "2026-09-13", "2026-09-18") == raw
    assert sectors.normalise_symbol(raw[0]["symbol"]) == "BBCA"
