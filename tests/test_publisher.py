from __future__ import annotations

import pandas as pd
import pytest

from autopost import publisher
from autopost.__main__ import make_client
from autopost.store import COLUMNS, load_queue, now_iso, save_queue
from autopost.threads_api import ThreadsClient


class FakeClient:
    def __init__(self, fail_on_call: int | None = None):
        self.posts: list[dict] = []
        self.replies: list[dict] = []
        self.calls = 0
        self.fail_on_call = fail_on_call

    def publish_text(self, text, reply_to_id=None):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("網路斷線")
        post_id = f"t{self.calls}"
        if reply_to_id:
            self.replies.append({"id": post_id, "text": text, "replied_to": {"id": reply_to_id}})
        else:
            self.posts.append({"id": post_id, "text": text})
        return post_id

    def recent_posts(self, limit=25):
        return self.posts[::-1]

    def recent_replies(self, limit=25):
        return self.replies[::-1]


def make_queue(path, rows):
    queue = pd.DataFrame([{col: "" for col in COLUMNS} | row for row in rows])
    save_queue(queue, path)
    return path


@pytest.fixture
def queue_path(tmp_path):
    return tmp_path / "posts.csv"


def test_dry_run_changes_nothing(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "approved", "text": "你好"}])
    before = queue_path.read_text()
    log = publisher.run(queue_path, None, live=False)
    assert queue_path.read_text() == before
    assert "[試跑]" in log[0]


def test_posts_root_and_reply_then_marks_posted(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "approved", "text": "第一段", "reply_text": "第二段"}])
    client = FakeClient()
    publisher.run(queue_path, client, live=True)
    row = load_queue(queue_path).iloc[0]
    assert (row.status, row.thread_id, row.reply_thread_id) == ("posted", "t1", "t2")
    assert client.replies[0]["replied_to"]["id"] == "t1"


def test_running_twice_never_reposts(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "approved", "text": "只發一次"}])
    client = FakeClient()
    publisher.run(queue_path, client, live=True)
    publisher.run(queue_path, client, live=True)
    assert len(client.posts) == 1


def test_only_max_posts_per_run(queue_path):
    make_queue(queue_path, [{"post_id": f"p{i}", "status": "approved", "text": f"完全不同的主題 {i} " * (i + 1)} for i in range(3)])
    client = FakeClient()
    publisher.run(queue_path, client, live=True, max_posts=1)
    assert load_queue(queue_path).status.tolist() == ["posted", "approved", "approved"]


def test_crash_after_root_only_posts_reply_on_retry(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "approved", "text": "第一段", "reply_text": "第二段"}])
    client = FakeClient(fail_on_call=2)
    with pytest.raises(RuntimeError):
        publisher.run(queue_path, client, live=True)
    row = load_queue(queue_path).iloc[0]
    assert (row.status, row.thread_id, row.reply_thread_id) == ("publishing", "t1", "")

    client.fail_on_call = None
    publisher.run(queue_path, client, live=True)
    assert len(client.posts) == 1 and len(client.replies) == 1
    assert load_queue(queue_path).iloc[0].status == "posted"


def test_recovery_finds_post_that_was_sent_but_not_recorded(queue_path):
    # 模擬：API 已發出，但程式在存 thread_id 前就當掉
    make_queue(queue_path, [{"post_id": "p1", "status": "publishing", "text": "已經發出去了"}])
    client = FakeClient()
    client.posts.append({"id": "t99", "text": "已經發出去了"})
    publisher.run(queue_path, client, live=True)
    row = load_queue(queue_path).iloc[0]
    assert (row.status, row.thread_id) == ("posted", "t99")
    assert client.calls == 0


def test_recovery_ignores_old_reply_with_same_text_under_other_post(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "publishing", "text": "新文", "reply_text": "連結在這", "thread_id": "t50"}])
    client = FakeClient()
    client.replies.append({"id": "t10", "text": "連結在這", "replied_to": {"id": "t5"}})
    publisher.run(queue_path, client, live=True)
    row = load_queue(queue_path).iloc[0]
    assert row.reply_thread_id != "t10"
    assert client.replies[-1]["replied_to"]["id"] == "t50"


def test_duplicate_is_rejected_and_next_one_posts(queue_path):
    make_queue(queue_path, [
        {"post_id": "p1", "status": "posted", "text": "雙 11 假特價教學", "posted_at": now_iso()},
        {"post_id": "p2", "status": "approved", "text": "雙11  假特價教學 https://s.shopee.tw/abc"},
        {"post_id": "p3", "status": "approved", "text": "租屋族收納最省錢的方法"},
    ])
    client = FakeClient()
    publisher.run(queue_path, client, live=True)
    queue = load_queue(queue_path).set_index("post_id")
    assert queue.at["p2", "status"] == "rejected"
    assert "重複" in queue.at["p2", "note"]
    assert queue.at["p3", "status"] == "posted"


def test_too_long_is_rejected(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "approved", "text": "字" * 501}])
    publisher.run(queue_path, FakeClient(), live=True)
    assert load_queue(queue_path).iloc[0].status == "rejected"


def test_check_flags_duplicate_between_pending_drafts(queue_path):
    make_queue(queue_path, [
        {"post_id": "p1", "status": "draft", "text": "一模一樣"},
        {"post_id": "p2", "status": "draft", "text": "一模一樣"},
    ])
    log = publisher.check(queue_path)
    assert log[0].startswith("✓") and log[1].startswith("✗")


def test_load_rejects_duplicate_post_id(queue_path):
    make_queue(queue_path, [{"post_id": "p1", "status": "draft", "text": "a"}, {"post_id": "p1", "status": "draft", "text": "b"}])
    with pytest.raises(ValueError, match="post_id 重複"):
        load_queue(queue_path)


def test_recovery_counts_toward_max_posts(queue_path):
    make_queue(queue_path, [
        {"post_id": "p1", "status": "publishing", "text": "第一篇未完成"},
        {"post_id": "p2", "status": "approved", "text": "第二篇完全不同的主題"},
    ])
    client = FakeClient()
    publisher.run(queue_path, client, live=True, max_posts=1)
    assert load_queue(queue_path).status.tolist() == ["posted", "approved"]


def test_empty_user_id_falls_back_to_me(monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "dummy-token")
    monkeypatch.setenv("THREADS_USER_ID", "")
    client = make_client()
    assert client.user_id == "me"
    assert ThreadsClient("dummy-token", user_id="   ").user_id == "me"
