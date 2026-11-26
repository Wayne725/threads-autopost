from __future__ import annotations

import json
import re
from typing import Any

import requests


class LLMAPIError(RuntimeError):
    pass


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _chat_completions_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def _models_url(base_url: str) -> str:
    cleaned = base_url.strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")]
    return f"{cleaned}/models"


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    match = JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("回傳不是 JSON 物件")
    return parsed


class OpenAICompatibleClient:
    """適用高科 iAI（LiteLLM）與任何 OpenAI 相容的 Chat Completions API。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.9,
        timeout: float = 60.0,
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.temperature = temperature
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def list_models(self) -> list[str]:
        resp = self.session.get(_models_url(self.base_url), headers=self._headers(), timeout=self.timeout)
        if not resp.ok:
            raise LLMAPIError(f"取得模型清單失敗 {resp.status_code}：{resp.text}")
        data = resp.json()
        return sorted(str(item["id"]) for item in data.get("data", []) if "id" in item)

    def _post_chat(self, payload: dict[str, Any]) -> requests.Response:
        return self.session.post(
            _chat_completions_url(self.base_url),
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )

    def generate_draft(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
        json_reminder = '\n\n請只輸出合法 JSON 物件，不要加其他解釋文字，格式：{"text": "...", "reply_text": "...", "topic": "..."}'
        base_payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt + json_reminder},
                {"role": "user", "content": user_prompt},
            ],
        }

        resp = self._post_chat({**base_payload, "response_format": {"type": "json_object"}})
        # 部分 LiteLLM 後端模型不支援 response_format，遇到 400 時自動退回一般模式重試一次
        if resp.status_code == 400:
            resp = self._post_chat(base_payload)

        if not resp.ok:
            raise LLMAPIError(f"LLM API 失敗 {resp.status_code}：{resp.text}")

        data = resp.json()
        try:
            raw_text = data["choices"][0]["message"]["content"]
            parsed = _parse_json_object(raw_text)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMAPIError(f"LLM 回傳格式異常：{data}") from exc

        return {
            "text": str(parsed.get("text", "")).strip(),
            "reply_text": str(parsed.get("reply_text", "")).strip(),
            "topic": str(parsed.get("topic", "")).strip(),
        }
