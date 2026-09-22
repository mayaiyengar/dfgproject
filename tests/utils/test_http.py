import httpx
import pytest
import respx

from utils.http import RateLimiter, build_http_client, fetch_robots_allows, request_with_retry, robots_allows


# --- request_with_retry -----------------------------------------------------


@respx.mock
def test_request_with_retry_succeeds_on_first_try():
    route = respx.get("https://example.gov/ok").mock(return_value=httpx.Response(200, json={"ok": True}))
    client = build_http_client()
    response = request_with_retry(client, "GET", "https://example.gov/ok")
    assert response.status_code == 200
    assert route.call_count == 1


@respx.mock
def test_request_with_retry_retries_on_500_then_succeeds():
    route = respx.get("https://example.gov/flaky").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    client = build_http_client()
    response = request_with_retry(client, "GET", "https://example.gov/flaky", max_retries=3)
    assert response.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_request_with_retry_retries_on_429():
    route = respx.get("https://example.gov/limited").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
    )
    client = build_http_client()
    response = request_with_retry(client, "GET", "https://example.gov/limited", max_retries=3)
    assert response.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_request_with_retry_does_not_retry_on_404():
    route = respx.get("https://example.gov/missing").mock(return_value=httpx.Response(404))
    client = build_http_client()
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "GET", "https://example.gov/missing", max_retries=3)
    assert route.call_count == 1


@respx.mock
def test_request_with_retry_gives_up_after_max_retries():
    route = respx.get("https://example.gov/always-down").mock(return_value=httpx.Response(503))
    client = build_http_client()
    with pytest.raises(httpx.HTTPStatusError):
        request_with_retry(client, "GET", "https://example.gov/always-down", max_retries=2)
    assert route.call_count == 3  # 1 initial + 2 retries


# --- RateLimiter -------------------------------------------------------------


def test_rate_limiter_allows_calls_under_the_limit_without_sleeping():
    fake_time = [0.0]
    sleeps: list[float] = []
    limiter = RateLimiter(3, 60, sleep_fn=sleeps.append, time_fn=lambda: fake_time[0])

    for _ in range(3):
        limiter.acquire()

    assert sleeps == []


def test_rate_limiter_sleeps_when_limit_exceeded_within_window():
    fake_time = [0.0]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        fake_time[0] += seconds

    limiter = RateLimiter(2, 60, sleep_fn=fake_sleep, time_fn=lambda: fake_time[0])
    limiter.acquire()  # t=0
    limiter.acquire()  # t=0
    limiter.acquire()  # exceeds 2 calls/60s -> must sleep

    assert sleeps == [60.0]


def test_rate_limiter_does_not_sleep_once_window_has_passed():
    fake_time = [0.0]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    limiter = RateLimiter(1, 10, sleep_fn=fake_sleep, time_fn=lambda: fake_time[0])
    limiter.acquire()  # t=0
    fake_time[0] = 11  # window has fully elapsed
    limiter.acquire()

    assert sleeps == []


def test_rate_limiter_rejects_nonpositive_config():
    with pytest.raises(ValueError):
        RateLimiter(0, 60)
    with pytest.raises(ValueError):
        RateLimiter(1, 0)


# --- robots.txt --------------------------------------------------------------

_ROBOTS_TXT = """
User-agent: *
Disallow: /private/
Allow: /
"""


def test_robots_allows_permitted_path():
    assert robots_allows(_ROBOTS_TXT, "https://example.gov/public/page", "DfG-Policy-Monitor/0.1") is True


def test_robots_disallows_blocked_path():
    assert robots_allows(_ROBOTS_TXT, "https://example.gov/private/page", "DfG-Policy-Monitor/0.1") is False


@respx.mock
def test_fetch_robots_allows_defaults_to_allow_when_missing():
    respx.get("https://example.gov/robots.txt").mock(return_value=httpx.Response(404))
    client = build_http_client()
    assert fetch_robots_allows(client, "https://example.gov/any/page", "DfG-Policy-Monitor/0.1") is True


@respx.mock
def test_fetch_robots_allows_respects_disallow():
    respx.get("https://example.gov/robots.txt").mock(return_value=httpx.Response(200, text=_ROBOTS_TXT))
    client = build_http_client()
    assert fetch_robots_allows(client, "https://example.gov/private/page", "DfG-Policy-Monitor/0.1") is False


@respx.mock
def test_fetch_robots_allows_defaults_to_allow_on_transport_error():
    respx.get("https://example.gov/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    client = build_http_client()
    assert fetch_robots_allows(client, "https://example.gov/any/page", "DfG-Policy-Monitor/0.1") is True
