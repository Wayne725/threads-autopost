import pandas as pd

from autopost.dedupe import find_duplicate
from autopost.store import TAIPEI

NOW = pd.Timestamp("2026-11-24 12:00", tz=TAIPEI)


def history(*rows):
    return pd.DataFrame([{"post_id": "", "text": "", "product_id": "", "posted_at": ""} | r for r in rows])


def draft(text, product_id=""):
    return pd.Series({"text": text, "product_id": product_id})


def test_exact_duplicate_ignores_spacing_case_and_urls():
    past = history({"post_id": "p1", "text": "Shopee 好物 https://a.com/1", "posted_at": "2026-11-01T12:00:00+08:00"})
    assert "完全重複" in find_duplicate(draft("shopee好物 https://b.com/2"), past, NOW)


def test_similar_text_is_flagged():
    past = history({"post_id": "p1", "text": "租屋族收納我花最少錢效果最大的一樣東西", "posted_at": "2026-11-01T12:00:00+08:00"})
    assert "相似度" in find_duplicate(draft("租屋族收納我花最少錢效果最好的一樣東西"), past, NOW)


def test_different_text_passes():
    past = history({"post_id": "p1", "text": "雙 11 假特價教學", "posted_at": "2026-11-01T12:00:00+08:00"})
    assert find_duplicate(draft("寵物用品平價替代清單"), past, NOW) is None


def test_product_cooldown():
    past = history({"post_id": "p1", "text": "完全不同的一篇文", "product_id": "123", "posted_at": "2026-11-20T12:00:00+08:00"})
    assert "14 天內" in find_duplicate(draft("另一個角度介紹同商品", "123"), past, NOW)


def test_product_allowed_after_cooldown():
    past = history({"post_id": "p1", "text": "完全不同的一篇文", "product_id": "123", "posted_at": "2026-10-01T12:00:00+08:00"})
    assert find_duplicate(draft("另一個角度介紹同商品", "123"), past, NOW) is None
