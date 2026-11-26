from __future__ import annotations

import json
from typing import Any

import requests

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.5-flash"

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "text": {"type": "STRING"},
        "reply_text": {"type": "STRING"},
        "topic": {"type": "STRING"},
    },
    "required": ["text", "reply_text", "topic"],
}


class GeminiAPIError(RuntimeError):
    pass


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.9,
        timeout: float = 60.0,
    ):
        self.api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.temperature = temperature
        self.timeout = timeout
        self.session = requests.Session()

    def generate_draft(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
        url = f"{GEMINI_API_BASE}/{self.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseSchema": DRAFT_SCHEMA,
            },
        }
        resp = self.session.post(
            url,
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        if not resp.ok:
            raise GeminiAPIError(f"Gemini API 失敗 {resp.status_code}：{resp.text}")

        data = resp.json()
        try:
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(raw_text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiAPIError(f"Gemini 回傳格式異常：{data}") from exc

        return {
            "text": str(parsed.get("text", "")).strip(),
            "reply_text": str(parsed.get("reply_text", "")).strip(),
            "topic": str(parsed.get("topic", "")).strip(),
        }
