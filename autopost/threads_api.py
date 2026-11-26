from __future__ import annotations

import time

import requests

GRAPH_URL = "https://graph.threads.net"
API_URL = f"{GRAPH_URL}/v1.0"


class ThreadsAPIError(RuntimeError):
    pass


class ThreadsClient:
    def __init__(self, access_token: str, user_id: str = "me", publish_delay: float = 3.0, timeout: float = 30.0):
        self.access_token = access_token.strip()
        self.user_id = user_id.strip() or "me"
        self.publish_delay = publish_delay
        self.timeout = timeout
        self.session = requests.Session()

    def _request(self, method: str, url: str, **params) -> dict:
        resp = self.session.request(method, url, params={**params, "access_token": self.access_token}, timeout=self.timeout)
        if not resp.ok:
            raise ThreadsAPIError(f"{method} {url.replace(GRAPH_URL, '')} 失敗 {resp.status_code}：{resp.text}")
        return resp.json()

    def publish_text(self, text: str, reply_to_id: str | None = None) -> str:
        params = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        container = self._request("POST", f"{API_URL}/{self.user_id}/threads", **params)
        # Meta 建議建立容器後稍等再發布
        time.sleep(self.publish_delay)
        return self._request("POST", f"{API_URL}/{self.user_id}/threads_publish", creation_id=container["id"])["id"]

    def recent_posts(self, limit: int = 25) -> list[dict]:
        return self._request("GET", f"{API_URL}/{self.user_id}/threads", fields="id,text,timestamp", limit=limit).get("data", [])

    def recent_replies(self, limit: int = 25) -> list[dict]:
        return self._request(
            "GET", f"{API_URL}/{self.user_id}/replies", fields="id,text,timestamp,replied_to", limit=limit
        ).get("data", [])

    def me(self) -> dict:
        return self._request("GET", f"{API_URL}/me", fields="id,username")

    def refresh_token(self) -> dict:
        """長效 token 有效 60 天；回傳 {'access_token', 'expires_in'}。"""
        return self._request("GET", f"{GRAPH_URL}/refresh_access_token", grant_type="th_refresh_token")
