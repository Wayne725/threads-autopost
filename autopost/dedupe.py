from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

import pandas as pd

URL_OR_SPACE = re.compile(r"https?://\S+|\s+")


def normalize(text: str) -> str:
    return URL_OR_SPACE.sub("", text).lower()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def find_duplicate(
    draft: pd.Series,
    history: pd.DataFrame,
    now: pd.Timestamp,
    threshold: float = 0.8,
    recent_count: int = 30,
    cooldown_days: int = 14,
) -> str | None:
    """history 是已發（或發送中）的貼文；回傳重複原因，沒問題回傳 None。"""
    if history.empty:
        return None

    posted_at = pd.to_datetime(history.posted_at, utc=True, errors="coerce", format="ISO8601")
    history = history.assign(posted_at_ts=posted_at).sort_values("posted_at_ts", na_position="last")

    matches = history.post_id[history.text.map(text_hash) == text_hash(draft.text)]
    if not matches.empty:
        return f"內容與 {matches.iloc[0]} 完全重複"

    for _, past in history.tail(recent_count).iterrows():
        score = similarity(draft.text, past.text)
        if score > threshold:
            return f"內容與 {past.post_id} 相似度 {score:.0%}"

    if draft.product_id:
        cutoff = now.tz_convert("UTC") - pd.Timedelta(days=cooldown_days)
        is_recent = (history.posted_at_ts > cutoff) | history.posted_at_ts.isna()
        recent_products = history.product_id[is_recent]
        if draft.product_id in set(recent_products):
            return f"商品 {draft.product_id} 在 {cooldown_days} 天內推過"

    return None
