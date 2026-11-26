from __future__ import annotations

import pandas as pd
import pytest

from autopost import generator
from autopost.__main__ import make_llm_client
from autopost.gemini_api import GeminiClient
from autopost.llm_api import OpenAICompatibleClient, _chat_completions_url, _models_url, _parse_json_object
from autopost.store import COLUMNS, PRODUCT_COLUMNS, load_queue, now_iso, save_queue


class FakeLLM:
    def __init__(self, responses: list[dict[str, str]]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_draft(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
        self.prompts.append(user_prompt)
        if not self.responses:
            raise AssertionError("FakeLLM 沒有剩餘回應")
        return self.responses.pop(0)


def write_queue(path, rows):
    df = pd.DataFrame([{col: "" for col in COLUMNS} | row for row in rows])
    save_queue(df, path)
    return path


def write_products(path, rows):
    df = pd.DataFrame([{col: "" for col in PRODUCT_COLUMNS} | row for row in rows])
    df.to_csv(path, index=False)
    return path


def test_skips_when_queue_above_min_threshold(tmp_path):
    q = write_queue(
        tmp_path / "posts.csv",
        [{"post_id": f"p{i:03d}", "status": "approved", "text": f"獨立主題 {i}"} for i in range(1, 7)],
    )
    llm = FakeLLM([])
    log = generator.run(q, llm, products_path=tmp_path / "products.csv", min_queue=6, target_queue=10)
    assert "庫存充足" in log[0]
    assert llm.prompts == []


def test_refills_with_3_to_1_ratio_and_sequential_ids(tmp_path):
    q = write_queue(
        tmp_path / "posts.csv",
        [
            {"post_id": "p001", "status": "posted", "text": "第一篇純乾貨", "posted_at": now_iso()},
            {"post_id": "p002", "status": "approved", "text": "第二篇省錢觀念"},
            {"post_id": "p003", "status": "approved", "text": "第三篇評價篩選"},
        ],
    )
    p = write_products(
        tmp_path / "products.csv",
        [
            {
                "product_id": "prod-a",
                "name": "折疊收納箱",
                "url": "https://s.shopee.tw/AAA111",
                "category": "租屋收納",
                "highlights": "可折疊省空間、承重穩",
                "audience": "租屋族",
                "enabled": "true",
            }
        ],
    )
    llm = FakeLLM(
        [
            # 第 1 篇應觸發分潤（前面已有 3 篇連續純乾貨）
            {
                "text": "租屋處衣櫃永遠爆滿，後來發現問題是垂直空間都空著 👇",
                "reply_text": "用可折疊收納箱分層疊起來，搬家也能直接壓平帶走。\nhttps://s.shopee.tw/AAA111\n（含分潤連結）",
                "topic": "租屋收納箱",
            },
            # 第 2 篇回到純乾貨
            {
                "text": "網購開箱我現在一定做一個 30 秒小動作，救過我三次退貨 👇",
                "reply_text": "一鏡到底先拍物流單號再拆封，遇過少寄或破損直接附上影片，賣家跟平台都沒辦法推託。",
                "topic": "開箱錄影自保",
            },
        ]
    )

    log = generator.run(q, llm, products_path=p, min_queue=3, target_queue=4, status="approved", seed=42)
    queue = load_queue(q)
    assert queue.post_id.tolist() == ["p001", "p002", "p003", "p004", "p005"]
    assert queue.loc[3, "product_id"] == "prod-a"
    assert queue.loc[3, "status"] == "approved"
    assert queue.loc[4, "product_id"] == ""
    assert any("新增 2/2 篇" in line for line in log)


def test_retries_on_duplicate_and_hallucinated_url(tmp_path):
    q = write_queue(
        tmp_path / "posts.csv",
        [{"post_id": "p001", "status": "posted", "text": "買衛生紙一定要算每抽單價", "posted_at": now_iso()}],
    )
    llm = FakeLLM(
        [
            # 第 1 次：重複內容
            {"text": "買衛生紙一定要算每抽單價", "reply_text": "重複了", "topic": "重複"},
            # 第 2 次：純乾貨文亂塞網址
            {"text": "這招超商取貨省運費超好用", "reply_text": "看這裡 https://example.com/fake", "topic": "假連結"},
            # 第 3 次：全新合格
            {"text": "超商取貨和蝦皮店到店怎麼選才不會等太久？", "reply_text": "急用選四大超商，不急的小日用品選店到店省運費。", "topic": "物流選擇"},
        ]
    )

    log = generator.run(q, llm, products_path=tmp_path / "missing.csv", count=1, status="approved", seed=1)
    queue = load_queue(q)
    assert len(queue) == 2
    assert queue.loc[1, "post_id"] == "p002"
    assert "超商取貨" in queue.loc[1, "text"]
    assert len(llm.prompts) == 3
    assert "完全重複" in llm.prompts[1]
    assert "不可包含網址" in llm.prompts[2]
    assert any("[重試 1/3]" in line for line in log)
    assert any("[重試 2/3]" in line for line in log)


def test_product_on_cooldown_falls_back_to_pure_content(tmp_path):
    q = write_queue(
        tmp_path / "posts.csv",
        [
            {
                "post_id": "p001",
                "status": "approved",
                "text": "舊分潤文沒填 product_id",
                "reply_text": "這裡買 https://s.shopee.tw/USED99 （含分潤連結）",
            },
            {"post_id": "p002", "status": "approved", "text": "純乾貨一"},
            {"post_id": "p003", "status": "approved", "text": "純乾貨二"},
            {"post_id": "p004", "status": "approved", "text": "純乾貨三"},
        ],
    )
    p = write_products(
        tmp_path / "products.csv",
        [
            {
                "product_id": "used-item",
                "name": "已推過的商品",
                "url": "https://s.shopee.tw/USED99",
                "category": "日用品",
                "highlights": "測試",
                "audience": "所有人",
                "enabled": "true",
            }
        ],
    )
    llm = FakeLLM(
        [
            {
                "text": "網購退貨最常踩的一個坑，其實跟商品本身無關 👇",
                "reply_text": "很多人沒注意鑑賞期不是試用期，拆封耗材或剪吊牌後很容易被拒退。",
                "topic": "退貨注意",
            }
        ]
    )

    generator.run(q, llm, products_path=p, count=1, status="approved", seed=7)
    queue = load_queue(q)
    assert queue.iloc[-1].product_id == ""
    assert "純乾貨" in llm.prompts[0]


def test_dry_run_does_not_write_file(tmp_path):
    q = write_queue(tmp_path / "posts.csv", [{"post_id": "p001", "status": "posted", "text": "歷史貼文", "posted_at": now_iso()}])
    before = q.read_text()
    llm = FakeLLM([{"text": "全新的購物省錢習慣分享", "reply_text": "先把常買日用品單價記在備忘錄。", "topic": "備忘錄比價"}])

    log = generator.run(q, llm, products_path=tmp_path / "missing.csv", count=1, dry_run=True, seed=2)
    assert q.read_text() == before
    assert any("[試產]" in line for line in log)


def test_make_llm_client_prefers_openai_compatible_for_nkust_iai(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-nkust-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://iai.example.edu.tw/v1/")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    client = make_llm_client()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_key == "sk-nkust-key"
    assert client.base_url == "https://iai.example.edu.tw/v1"
    assert client.model == "gpt-4o-mini"


def test_make_llm_client_falls_back_to_gemini(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    client = make_llm_client()
    assert isinstance(client, GeminiClient)
    assert client.model == "gemini-2.5-flash"


def test_openai_compatible_url_normalization_and_json_parsing():
    assert _chat_completions_url("https://iai.example.edu.tw/v1/") == "https://iai.example.edu.tw/v1/chat/completions"
    assert (
        _chat_completions_url("https://iai.example.edu.tw/v1/chat/completions")
        == "https://iai.example.edu.tw/v1/chat/completions"
    )
    assert _models_url("https://iai.example.edu.tw/v1/chat/completions") == "https://iai.example.edu.tw/v1/models"

    fenced = '這是結果：\n```json\n{"text": "第一段", "reply_text": "第二段", "topic": "主題"}\n```'
    assert _parse_json_object(fenced) == {"text": "第一段", "reply_text": "第二段", "topic": "主題"}


def test_openai_compatible_retries_without_response_format_on_400(monkeypatch):
    calls = []

    class FakeResp:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self.ok = 200 <= status_code < 300
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if "response_format" in json:
            return FakeResp(400, {"error": "response_format not supported"})
        return FakeResp(
            200,
            {"choices": [{"message": {"content": '{"text": "成功", "reply_text": "內容", "topic": "測試"}'}}]},
        )

    client = OpenAICompatibleClient(api_key="sk-1", base_url="https://iai.example.edu.tw/v1", model="llama")
    monkeypatch.setattr(client.session, "post", fake_post)
    result = client.generate_draft("sys", "user")
    assert result == {"text": "成功", "reply_text": "內容", "topic": "測試"}
    assert len(calls) == 2 and "response_format" not in calls[1]
