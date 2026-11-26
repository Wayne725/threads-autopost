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


def test_affiliate_url_cooldown_catches_missing_product_id():
    past = history({
        "post_id": "p10",
        "text": "湊免運推薦濕紙巾",
        "reply_text": "連結 https://s.shopee.tw/2gCxPDbu3k （含分潤連結）",
        "posted_at": "2026-11-20T12:00:00+08:00",
    })
    candidate = pd.Series({
        "text": "完全不同的標題講擦手紙",
        "reply_text": "點這裡 https://s.shopee.tw/2gCxPDbu3k （含分潤連結）",
        "product_id": "",
    })
    assert "連結" in find_duplicate(candidate, past, NOW)


def test_hook_threshold_flags_repeated_opening_line():
    past = history({
        "post_id": "p1",
        "text": "買東西看評價，我現在都跳過 5 顆星，先看 3 顆星。\n\n第一段收尾",
        "posted_at": "2026-11-20T12:00:00+08:00",
    })
    candidate = pd.Series({
        "text": "買東西看評價，我現在都跳過 5 顆星，改先看 3 顆星。\n\n後面是完全不同的新論點與分析內容",
        "reply_text": "",
        "product_id": "",
    })
    assert "開頭" in find_duplicate(candidate, past, NOW, threshold=0.8, hook_threshold=0.72)
