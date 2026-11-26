from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

import pandas as pd

URL_RE = re.compile(r"https?://\S+")
URL_OR_SPACE = re.compile(r"https?://\S+|\s+")


def normalize(text: str) -> str:
    return URL_OR_SPACE.sub("", text).lower()


def extract_urls(text: str) -> set[str]:
    return {m.rstrip(".,;:!?。，；：！？)）]】") for m in URL_RE.findall(text or "")}


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _combined_text(row: pd.Series) -> str:
    reply = row.get("reply_text", "") if hasattr(row, "get") else ""
    return f"{row.text}\n{reply}" if reply else row.text


def find_duplicate(
    draft: pd.Series,
    history: pd.DataFrame,
    now: pd.Timestamp,
    threshold: float = 0.8,
    recent_count: int = 30,
    cooldown_days: int = 14,
    hook_threshold: float | None = None,
) -> str | None:
    """history 是已發（或發送中）的貼文；回傳重複原因，沒問題回傳 None。"""
    if history.empty:
        return None

    posted_at = pd.to_datetime(history.posted_at, utc=True, errors="coerce", format="ISO8601")
    history = history.assign(posted_at_ts=posted_at).sort_values("posted_at_ts", na_position="last")

    matches = history.post_id[history.text.map(text_hash) == text_hash(draft.text)]
    if not matches.empty:
        return f"內容與 {matches.iloc[0]} 完全重複"

    draft_combined = _combined_text(draft)
    draft_hook = first_line(draft.text)

    for _, past in history.tail(recent_count).iterrows():
        score = similarity(draft.text, past.text)
        if score > threshold:
            return f"內容與 {past.post_id} 相似度 {score:.0%}"

        past_combined = _combined_text(past)
        if draft_combined != draft.text and past_combined != past.text:
            combined_score = similarity(draft_combined, past_combined)
            if combined_score > threshold:
                return f"全文與 {past.post_id} 相似度 {combined_score:.0%}"

        if hook_threshold is not None and len(normalize(draft_hook)) >= 10:
            past_hook = first_line(past.text)
            if len(normalize(past_hook)) >= 10:
                hook_score = similarity(draft_hook, past_hook)
                if hook_score > hook_threshold:
                    return f"開頭與 {past.post_id} 相似度 {hook_score:.0%}"

    cutoff = now.tz_convert("UTC") - pd.Timedelta(days=cooldown_days)
    is_recent = (history.posted_at_ts > cutoff) | history.posted_at_ts.isna()
    recent_history = history[is_recent]

    if draft.product_id:
        if draft.product_id in set(recent_history.product_id):
            return f"商品 {draft.product_id} 在 {cooldown_days} 天內推過"

    draft_urls = extract_urls(draft_combined)
    if draft_urls:
        recent_urls: set[str] = set()
        for _, past in recent_history.iterrows():
            recent_urls |= extract_urls(_combined_text(past))
        overlap = sorted(draft_urls & recent_urls)
        if overlap:
            return f"連結 {overlap[0]} 在 {cooldown_days} 天內推過"

    return None
