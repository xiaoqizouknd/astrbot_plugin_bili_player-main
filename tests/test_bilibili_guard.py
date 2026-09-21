"""防风控单元测试：冷却、重试、缓存、短链、分页与 buvid3。"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.bilibili import (
    BilibiliApiError,
    BilibiliClient,
    BilibiliCooldownError,
    extract_bilibili_short_links,
)


class FakeResponse:
    def __init__(
        self,
        *,
        payload: object | None = None,
        text: str = "{}",
        status: int = 200,
        url: str = "https://www.bilibili.com/video/BV1fixture/",
    ) -> None:
        self.status = status
        self.url = url
        self.cookies: dict[str, object] = {}
        self._text = text
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self) -> str:
        import json

        if self._payload is not None:
            return json.dumps(self._payload)
        return self._text


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self.responses.pop(0)


class BilibiliGuardTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, responses: list[FakeResponse]) -> BilibiliClient:
        client = BilibiliClient(session=FakeSession(responses))
        client._session_ref = client._session  # keep the fake reachable
        return client

    async def test_transient_500_retries_once_and_recovers(self) -> None:
        client = self._client(
            [
                FakeResponse(status=500),
                FakeResponse(payload={"code": 0, "data": {"ok": True}}),
            ]
        )
        with patch("core.bilibili.random.uniform", return_value=0.0):
            payload, _ = await client._request_json("https://api.bilibili.com/x/test")
        self.assertEqual(payload["code"], 0)
        self.assertEqual(len(client._session.calls), 2)
        self.assertEqual(client._consecutive_failures, 0)

    async def test_consecutive_failures_enter_cooldown(self) -> None:
        failing = [FakeResponse(status=500) for _ in range(20)]
        client = self._client(failing)
        with patch("core.bilibili.random.uniform", return_value=0.0):
            for _ in range(3):
                with self.assertRaises(Exception):
                    await client._request_json("https://api.bilibili.com/x/test")
            with self.assertRaises(BilibiliCooldownError):
                await client._request_json("https://api.bilibili.com/x/test")
        calls_after_cooldown = len(client._session.calls)
        with self.assertRaises(BilibiliCooldownError):
            await client._request_json("https://api.bilibili.com/x/test")
        # 冷却期内不再发起任何新请求。
        self.assertEqual(len(client._session.calls), calls_after_cooldown)

    async def test_risk_control_code_cools_down_without_retry(self) -> None:
        client = self._client([FakeResponse(payload={"code": -412, "message": "x"})])
        with patch("core.bilibili.random.uniform", return_value=0.0):
            with self.assertRaises(BilibiliApiError) as caught:
                await client._request_json("https://api.bilibili.com/x/test")
        self.assertEqual(caught.exception.code, -412)
        self.assertIn("风控", str(caught.exception))
        self.assertEqual(len(client._session.calls), 1)
        # 立即冷却：下一次请求不再打点。
        with self.assertRaises(BilibiliCooldownError):
            await client._request_json("https://api.bilibili.com/x/test")
        self.assertEqual(len(client._session.calls), 1)

    async def test_success_resets_failure_count(self) -> None:
        client = self._client(
            [
                FakeResponse(status=500),
                FakeResponse(status=500),
                FakeResponse(payload={"code": 0, "data": {}}),
            ]
        )
        with patch("core.bilibili.random.uniform", return_value=0.0):
            with self.assertRaises(Exception):
                await client._request_json("https://api.bilibili.com/x/test")
            await client._request_json("https://api.bilibili.com/x/test")
        self.assertEqual(client._consecutive_failures, 0)

    async def test_detail_cache_serves_repeat_requests_without_network(self) -> None:
        payload = {
            "bvid": "BV1fixture",
            "title": "缓存视频",
            "owner": {"name": "上传者"},
            "pages": [{"cid": 7, "page": 1, "part": "正片", "duration": 269}],
        }

        class FixtureClient(BilibiliClient):
            def __init__(self) -> None:
                super().__init__(session=object())
                self.view_calls: list[dict[str, object]] = []

            async def _wbi_data(self, endpoint, params):
                self.view_calls.append(dict(params))
                return payload

        client = FixtureClient()
        first = await client.get_video("BV1fixture")
        second = await client.get_video("bv1fixture")
        self.assertIs(first, second)
        self.assertEqual(len(client.view_calls), 1)
        self.assertEqual(first.pages[0].cid, 7)

    async def test_search_videos_passes_bounded_page(self) -> None:
        class FixtureClient(BilibiliClient):
            def __init__(self) -> None:
                super().__init__(session=object())
                self.params: dict[str, object] = {}

            async def _wbi_data(self, endpoint, params):
                self.params = dict(params)
                return {"result": []}

        client = FixtureClient()
        await client.search_videos("晴天", page=3)
        self.assertEqual(client.params["page"], 3)
        await client.search_videos("晴天", page=99)
        self.assertEqual(client.params["page"], 20)

    async def test_short_link_resolution_follows_redirect(self) -> None:
        client = self._client(
            [FakeResponse(url="https://www.bilibili.com/video/BV1Q541167Qg/")]
        )
        final_url = await client.resolve_short_link("https://b23.tv/abcDef")
        self.assertEqual(final_url, "https://www.bilibili.com/video/BV1Q541167Qg/")
        self.assertEqual(len(client._session.calls), 1)

    async def test_short_link_rejects_non_short_hosts_without_network(self) -> None:
        client = self._client([])
        self.assertIsNone(
            await client.resolve_short_link("https://example.com/x")
        )
        self.assertIsNone(await client.resolve_short_link("http://b23.tv/abc"))
        self.assertEqual(len(client._session.calls), 0)

    def test_extract_short_links_is_first_seen_and_deduplicated(self) -> None:
        text = "https://b23.tv/abc 再 https://bili2233.cn/def 和 https://b23.tv/abc"
        self.assertEqual(
            extract_bilibili_short_links(text),
            ("https://b23.tv/abc", "https://bili2233.cn/def"),
        )
        self.assertEqual(extract_bilibili_short_links(None), ())

    async def test_anonymous_requests_carry_a_stable_buvid3_cookie(self) -> None:
        client = self._client([FakeResponse(payload={"code": 0, "data": {}})])
        await client._request_json("https://api.bilibili.com/x/test")
        headers = client._session.calls[0]["headers"]
        assert isinstance(headers, dict)
        cookie = str(headers.get("Cookie", ""))
        self.assertIn("buvid3=", cookie)
        self.assertTrue(cookie.endswith("infoc"))


if __name__ == "__main__":
    unittest.main()
