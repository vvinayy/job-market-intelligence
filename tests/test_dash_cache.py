"""An API error must not be cached. It used to be: api_get() caught the
HTTPError and returned [], and st.cache_data cached that [] for 30 minutes,
so a chart stayed blank long after the API had recovered."""

import requests

import dash_common as dc


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def test_http_error_is_retried_not_served_from_cache(monkeypatch):
    dc._fetch.clear()
    replies = iter([_Resp(500, None), _Resp(200, [{"name": "AWS"}])])
    monkeypatch.setattr(dc._SESSION, "get", lambda *a, **k: next(replies))

    assert dc.api_get("/test-cache") == []                   # degrades as before
    assert dc.api_get("/test-cache") == [{"name": "AWS"}]    # retried, not cached []
