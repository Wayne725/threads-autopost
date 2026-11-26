from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")

COLUMNS = [
    "post_id",
    "status",
    "text",
    "reply_text",
    "product_id",
    "note",
    "thread_id",
    "reply_thread_id",
    "created_at",
    "posted_at",
]

PRODUCT_COLUMNS = [
    "product_id",
    "name",
    "url",
    "category",
    "highlights",
    "audience",
    "enabled",
]

STATUSES = {"draft", "approved", "publishing", "posted", "rejected"}
POST_ID_RE = re.compile(r"^p(\d+)$")


def now_iso() -> str:
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def next_post_id(queue: pd.DataFrame) -> str:
    max_num = 0
    for pid in queue.post_id:
        match = POST_ID_RE.match(str(pid).strip())
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"p{max_num + 1:03d}"


def load_queue(path: str | Path) -> pd.DataFrame:
    queue = (
        pd.read_csv(path, dtype=str, keep_default_na=False)
        .reindex(columns=COLUMNS, fill_value="")
        .reset_index(drop=True)
    )
    unknown = set(queue.status) - STATUSES
    if unknown:
        raise ValueError(f"未知的 status：{sorted(unknown)}，只能是 {sorted(STATUSES)}")
    dup_ids = queue.post_id[queue.post_id.duplicated()].tolist()
    if dup_ids:
        raise ValueError(f"post_id 重複：{dup_ids}")
    return queue


def save_queue(queue: pd.DataFrame, path: str | Path) -> None:
    # 先寫暫存檔再取代，避免寫到一半當掉把佇列弄壞
    tmp_path = f"{path}.tmp"
    queue.to_csv(tmp_path, index=False)
    os.replace(tmp_path, path)


def load_products(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    products = (
        pd.read_csv(file_path, dtype=str, keep_default_na=False)
        .reindex(columns=PRODUCT_COLUMNS, fill_value="")
        .reset_index(drop=True)
    )
    dup_ids = products.product_id[products.product_id.duplicated()].tolist()
    if dup_ids:
        raise ValueError(f"products.csv 的 product_id 重複：{dup_ids}")
    return products
