from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

from .dedupe import find_duplicate
from .store import TAIPEI, load_queue, now_iso, save_queue

MAX_LENGTH = 500


class Client(Protocol):
    def publish_text(self, text: str, reply_to_id: str | None = None) -> str: ...
    def recent_posts(self, limit: int = 25) -> list[dict]: ...
    def recent_replies(self, limit: int = 25) -> list[dict]: ...


def validate(row: pd.Series, history: pd.DataFrame, now: pd.Timestamp) -> str | None:
    if not row.text.strip():
        return "內文空白"
    for field in ("text", "reply_text"):
        if len(row[field]) > MAX_LENGTH:
            return f"{field} 有 {len(row[field])} 字，超過 {MAX_LENGTH} 字上限"
    return find_duplicate(row, history, now)


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _find_posted_root(client: Client, text: str) -> str | None:
    return next((p["id"] for p in client.recent_posts() if _same_text(p.get("text", ""), text)), None)


def _find_posted_reply(client: Client, text: str, parent_id: str) -> str | None:
    return next(
        (
            r["id"]
            for r in client.recent_replies()
            if r.get("replied_to", {}).get("id") == parent_id and _same_text(r.get("text", ""), text)
        ),
        None,
    )


def publish_row(queue: pd.DataFrame, idx: int, client: Client, path: str | Path, recovering: bool) -> None:
    """每一步都先存檔；中途當掉時，下次以 recovering=True 重跑會先查 Threads 上是否已發過，不會重發。"""
    queue.at[idx, "status"] = "publishing"
    save_queue(queue, path)

    text, reply_text = queue.at[idx, "text"], queue.at[idx, "reply_text"]

    if not queue.at[idx, "thread_id"]:
        existing = _find_posted_root(client, text) if recovering else None
        queue.at[idx, "thread_id"] = existing or client.publish_text(text)
        save_queue(queue, path)

    if reply_text and not queue.at[idx, "reply_thread_id"]:
        parent_id = queue.at[idx, "thread_id"]
        existing = _find_posted_reply(client, reply_text, parent_id) if recovering else None
        queue.at[idx, "reply_thread_id"] = existing or client.publish_text(reply_text, reply_to_id=parent_id)
        save_queue(queue, path)

    queue.at[idx, "status"] = "posted"
    queue.at[idx, "posted_at"] = now_iso()
    save_queue(queue, path)


def run(path: str | Path, client: Client | None, live: bool, max_posts: int = 1) -> list[str]:
    if live and client is None:
        raise ValueError("正式發文需要 client")

    queue = load_queue(path)
    now = pd.Timestamp.now(tz=TAIPEI)
    log = []

    sent = 0
    if live:
        for idx in queue.index[queue.status == "publishing"]:
            publish_row(queue, idx, client, path, recovering=True)
            log.append(f"[補完] {queue.at[idx, 'post_id']}：上次中斷，已補完發布")
            sent += 1

    for idx in queue.index[queue.status == "approved"]:
        if sent >= max_posts:
            break
        row = queue.loc[idx]
        history = queue[queue.status.isin(["posted", "publishing"])]
        reason = validate(row, history, now)

        if reason:
            log.append(f"[略過] {row.post_id}：{reason}")
            if live:
                queue.at[idx, "status"] = "rejected"
                queue.at[idx, "note"] = reason
                save_queue(queue, path)
            continue

        if live:
            publish_row(queue, idx, client, path, recovering=False)
            log.append(f"[已發] {row.post_id}：thread_id={queue.at[idx, 'thread_id']}")
        else:
            preview = row.text.replace("\n", " ")[:40]
            log.append(f"[試跑] 會發 {row.post_id}：{preview}…" + ("（含第二段）" if row.reply_text else ""))
        sent += 1

    if sent == 0 and not any(line.startswith("[略過]") for line in log):
        log.append("沒有 approved 的草稿可發")
    return log


def check(path: str | Path) -> list[str]:
    """檢查所有 draft / approved 草稿，不改任何東西。"""
    queue = load_queue(path)
    now = pd.Timestamp.now(tz=TAIPEI)
    pending = queue.index[queue.status.isin(["draft", "approved"])]
    log = []
    for position, idx in enumerate(pending):
        earlier_pending = queue.loc[pending[:position]]
        history = pd.concat([queue[queue.status.isin(["posted", "publishing"])], earlier_pending])
        reason = validate(queue.loc[idx], history, now)
        log.append(f"{'✗' if reason else '✓'} {queue.at[idx, 'post_id']} [{queue.at[idx, 'status']}] {reason or 'OK'}")
    return log or ["沒有待審草稿"]
