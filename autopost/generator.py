from __future__ import annotations

import random
from pathlib import Path
from typing import Protocol

import pandas as pd

from .dedupe import extract_urls, find_duplicate, first_line
from .publisher import MAX_LENGTH
from .store import COLUMNS, TAIPEI, load_products, load_queue, next_post_id, now_iso, save_queue

ACTIVE_STATUSES = ["posted", "publishing", "approved", "draft"]
GENERATION_SIMILARITY_THRESHOLD = 0.65
GENERATION_HOOK_THRESHOLD = 0.72
GENERATION_RECENT_COUNT = 50
PRODUCT_COOLDOWN_DAYS = 14

CONTENT_PILLARS: list[tuple[str, str]] = [
    ("比價與促銷識破", "拆解電商促銷常見套路、查歷史價格或比價技巧"),
    ("評價與賣場篩選", "怎麼從商品評價、賣場資訊、出貨地與回覆率避開雷店"),
    ("運費與免運策略", "運費陷阱、免運券使用時機、湊單到底划不划算"),
    ("折價券與蝦幣實測", "疊券順序、賣場券與平台券差別、蝦幣折抵怎麼算最省"),
    ("網購防衝動心法", "避免半夜亂買、購物車冷卻、預算控制與斷捨離"),
    ("網購開箱與退貨自保", "開箱錄影重點、尺寸色差踩雷、安心退與客服溝通技巧"),
    ("日用消耗品囤貨法則", "衛生紙/洗劑/個人用品怎麼算單價、避免囤到過期受潮"),
    ("租屋小資採購術", "租屋族買家具小物不踩雷、搬家好帶走又高 CP 值的挑法"),
    ("3C 配件避雷指南", "充電線、行動電源、保護貼、手機殼等配件挑選重點"),
    ("換季與節日買物時機", "什麼時候買換季家電、保暖或消暑用品最容易撿便宜"),
    ("賣場話術與標題拆解", "看懂「官方直營」「現貨秒出」「台灣出貨」「升級款」的真實含義"),
    ("網購取貨與物流習慣", "超商取貨、蝦皮店到店、宅配的時效差異與省運費搭配"),
    ("合購與湊滿額技巧", "跟室友或同事合買怎麼分攤運費與滿額折抵最不容易尷尬"),
    ("生活小痛點微改造", "不用花大錢就能明顯提升居家或書桌舒適度的小改變"),
    ("網購踩雷經驗互動", "用輕鬆問句邀請大家留言分享自己遇過的網購奇葩或省錢經驗"),
    ("平價替代挑選思維", "哪些東西買平價款就夠用、哪些真的不能省錢買最便宜的"),
]

POST_FORMATS: list[tuple[str, str]] = [
    ("條列清單型", "第一段用一句反差或痛點勾住人（結尾可加 👇），第二段條列 3 個具體可執行的重點"),
    ("反常識觀點型", "第一段提出一個顛覆多數人習慣的購物觀點，第二段解釋背後原因和實際做法"),
    ("互動提問型", "第一段分享自己的小習慣或困擾並丟出開放式問題，第二段補充 1-2 個延伸觀察或留空"),
    ("避雷故事型", "第一段講一個具體的網購踩雷或差點花冤枉錢的情境，第二段整理成 2-3 個避坑檢查點"),
    ("對比選擇型", "第一段對比兩種常見買法（A vs B），第二段拆解什麼情況該選哪一種"),
]

SYSTEM_PROMPT = """你是一位在台灣經營 Threads 的省錢網購與生活好物分享創作者，熟悉蝦皮購物、小資租屋生活與理性消費。

寫作風格要求：
- 使用道地台灣繁體中文與台灣用語（例如：超商、蝦皮、划算、踩雷、租屋、優惠券、免運）。
- 語氣自然像真人聊天、真誠分享實用經驗，不要像業配公關稿，也不要用過度誇張的驚嘆號或「家人們」「寶子們」等大陸用語。
- 不要加 #hashtag 標籤。
- 採用 Threads 兩段式串文：
  - text（第一段）：短而有勾子，約 40～140 字，讓人想點開看下一段。
  - reply_text（第二段）：承接第一段的乾貨、步驟、結論或商品推薦，約 80～280 字（互動提問型可留空）。
  - 兩段都必須嚴格小於 450 字（絕對不可超過 500 字）。
- 絕不捏造商品規格、價格或網址；沒有提供網址時，內文不可出現任何 http/https 連結。
- 必須嚴格避開「已發過或待發的貼文清單」中的主題、切入點、開場句型與結論，確保每篇都有全新角度。

回傳 JSON，包含三個欄位：text、reply_text、topic（一句 10 字內的主題摘要）。"""


class LLMClient(Protocol):
    def generate_draft(self, system_prompt: str, user_prompt: str) -> dict[str, str]: ...


def _is_affiliate_row(row: pd.Series) -> bool:
    if str(row.get("product_id", "")).strip():
        return True
    combined = f"{row.get('text', '')}\n{row.get('reply_text', '')}"
    return "s.shopee.tw" in combined


def _consecutive_content_count(queue: pd.DataFrame) -> int:
    active = queue[queue.status.isin(ACTIVE_STATUSES)]
    count = 0
    for _, row in active.iloc[::-1].iterrows():
        if _is_affiliate_row(row):
            break
        count += 1
    return count


def _select_product(
    products: pd.DataFrame,
    history: pd.DataFrame,
    now: pd.Timestamp,
    cooldown_days: int = PRODUCT_COOLDOWN_DAYS,
) -> pd.Series | None:
    if products.empty:
        return None

    enabled = products[products.enabled.str.strip().str.lower().isin(["true", "1", "yes", "y", ""])]
    enabled = enabled[(enabled.product_id.str.strip() != "") & (enabled.url.str.strip() != "")]
    if enabled.empty:
        return None

    cutoff = now.tz_convert("UTC") - pd.Timedelta(days=cooldown_days)
    last_used: dict[str, pd.Timestamp] = {}
    blocked_ids: set[str] = set()
    blocked_urls: set[str] = set()

    if not history.empty:
        posted_ts = pd.to_datetime(history.posted_at, utc=True, errors="coerce", format="ISO8601")
        for (_, row), ts in zip(history.iterrows(), posted_ts):
            pid = str(row.get("product_id", "")).strip()
            urls = extract_urls(f"{row.get('text', '')}\n{row.get('reply_text', '')}")
            is_recent = pd.isna(ts) or ts > cutoff
            if is_recent:
                if pid:
                    blocked_ids.add(pid)
                blocked_urls |= urls
            if pid and not pd.isna(ts):
                if pid not in last_used or ts > last_used[pid]:
                    last_used[pid] = ts

    eligible = []
    for _, prod in enabled.iterrows():
        pid = prod.product_id.strip()
        url = prod.url.strip()
        if pid in blocked_ids or url in blocked_urls:
            continue
        ts = last_used.get(pid)
        sort_key = (0, pd.Timestamp.min.tz_localize("UTC")) if ts is None else (1, ts)
        eligible.append((sort_key, prod))

    if not eligible:
        return None

    eligible.sort(key=lambda item: item[0])
    return eligible[0][1]


def _pick_pillar_and_format(
    history: pd.DataFrame,
    used_pillars: set[str],
    rng: random.Random,
) -> tuple[tuple[str, str], tuple[str, str]]:
    recent_blob = "\n".join(history.tail(15).text.tolist()) if not history.empty else ""
    candidates = [p for p in CONTENT_PILLARS if p[0] not in used_pillars]
    if not candidates:
        candidates = list(CONTENT_PILLARS)

    # 優先挑最近 15 篇沒出現過關鍵字的主題
    fresh = [p for p in candidates if not any(tok in recent_blob for tok in p[0].split("與"))]
    pool = fresh or candidates
    pillar = rng.choice(pool)
    fmt = rng.choice(POST_FORMATS)
    return pillar, fmt


def _format_recent_history(history: pd.DataFrame, limit: int = 30) -> str:
    if history.empty:
        return "（目前尚無歷史貼文）"
    lines = []
    for _, row in history.tail(limit).iterrows():
        hook = first_line(row.text)[:80]
        pid = str(row.get("product_id", "")).strip()
        tag = f" [商品:{pid}]" if pid else ""
        lines.append(f"- {row.post_id}{tag}：{hook}")
    return "\n".join(lines)


def _build_user_prompt(
    history: pd.DataFrame,
    product: pd.Series | None,
    pillar: tuple[str, str] | None,
    fmt: tuple[str, str],
    rejection_feedback: str | None = None,
) -> str:
    recent_block = _format_recent_history(history)
    parts = [
        "以下是已經發過或已排程的貼文開頭，請務必完全避開這些主題、切入點與類似句型：",
        recent_block,
        "",
    ]
    if product is not None:
        parts.extend(
            [
                "本次任務：寫一篇「自然帶出商品推薦」的兩段式 Threads 貼文。",
                f"- 商品名稱：{product['name']}",
                f"- 分類：{product['category']}",
                f"- 核心賣點：{product['highlights']}",
                f"- 適合族群：{product['audience']}",
                f"- 分潤連結：{product['url']}",
                f"- 建議格式：{fmt[0]}（{fmt[1]}）",
                "- 規則：",
                "  1. 第一段從真實生活痛點或使用情境切入，不要一開始就硬推銷，第一段不要放連結。",
                f"  2. 第二段分享挑選重點或實際解決方式，文末自然附上完整連結 `{product['url']}`，並在最後加上 `（含分潤連結）`。",
                "  3. 不要杜撰商品未提供的規格、折扣或價格。",
            ]
        )
    else:
        assert pillar is not None
        parts.extend(
            [
                "本次任務：寫一篇「純乾貨 / 省錢技巧 / 互動討論」的 Threads 貼文（不含任何商品推廣或網址）。",
                f"- 指定主題方向：{pillar[0]}（{pillar[1]}）",
                f"- 指定呈現格式：{fmt[0]}（{fmt[1]}）",
                "- 規則：內文與第二段都不可包含任何 http/https 連結，也不要提特定分潤商品。",
            ]
        )

    if rejection_feedback:
        parts.extend(
            [
                "",
                f"【重要修正】上一版草稿未通過檢查，原因：{rejection_feedback}",
                "請徹底換一個全新的切入角度與開場句子重新撰寫。",
            ]
        )

    return "\n".join(parts)


def validate_candidate(
    text: str,
    reply_text: str,
    product: pd.Series | None,
    history: pd.DataFrame,
    now: pd.Timestamp,
) -> str | None:
    if not text.strip():
        return "內文空白"
    if len(text) > MAX_LENGTH:
        return f"text 有 {len(text)} 字，超過 {MAX_LENGTH} 字上限"
    if len(reply_text) > MAX_LENGTH:
        return f"reply_text 有 {len(reply_text)} 字，超過 {MAX_LENGTH} 字上限"

    combined = f"{text}\n{reply_text}"
    urls = extract_urls(combined)

    if product is not None:
        expected_url = product["url"].strip()
        if expected_url not in combined:
            return f"分潤文缺少指定商品連結 {expected_url}"
        if "含分潤" not in combined:
            return "分潤文缺少「（含分潤連結）」揭露標示"
        extra_urls = urls - {expected_url}
        if extra_urls:
            return f"分潤文包含未授權的外部連結：{sorted(extra_urls)[0]}"
    else:
        if urls:
            return f"純乾貨文不可包含網址：{sorted(urls)[0]}"

    draft_row = pd.Series(
        {
            "text": text,
            "reply_text": reply_text,
            "product_id": product["product_id"].strip() if product is not None else "",
        }
    )
    return find_duplicate(
        draft_row,
        history,
        now,
        threshold=GENERATION_SIMILARITY_THRESHOLD,
        recent_count=GENERATION_RECENT_COUNT,
        cooldown_days=PRODUCT_COOLDOWN_DAYS,
        hook_threshold=GENERATION_HOOK_THRESHOLD,
    )


def run(
    queue_path: str | Path,
    client: LLMClient,
    products_path: str | Path = "products.csv",
    min_queue: int = 6,
    target_queue: int = 10,
    count: int | None = None,
    status: str = "approved",
    content_per_affiliate: int = 3,
    max_retries: int = 3,
    dry_run: bool = False,
    seed: int | None = None,
) -> list[str]:
    if status not in {"draft", "approved"}:
        raise ValueError(f"自動生成的 status 只能是 draft 或 approved，收到：{status}")

    queue = load_queue(queue_path)
    products = load_products(products_path)
    now = pd.Timestamp.now(tz=TAIPEI)
    rng = random.Random(seed)

    current_ready = int((queue.status == status).sum())
    if count is not None:
        needed = max(0, count)
    else:
        if current_ready >= min_queue:
            return [f"庫存充足（目前 {current_ready} 篇 {status}，門檻 {min_queue} 篇），不需補貨"]
        needed = max(0, target_queue - current_ready)

    if needed == 0:
        return ["不需要生成新草稿"]

    log: list[str] = []
    used_pillars: set[str] = set()
    created = 0

    for _ in range(needed):
        history = queue[queue.status.isin(ACTIVE_STATUSES)]
        consecutive = _consecutive_content_count(queue)
        want_affiliate = content_per_affiliate >= 0 and consecutive >= content_per_affiliate
        product = _select_product(products, history, now) if want_affiliate else None

        rejection_feedback: str | None = None
        accepted: dict[str, str] | None = None

        for attempt in range(1, max_retries + 1):
            pillar, fmt = _pick_pillar_and_format(history, used_pillars, rng)
            prompt = _build_user_prompt(
                history=history,
                product=product,
                pillar=None if product is not None else pillar,
                fmt=fmt,
                rejection_feedback=rejection_feedback,
            )
            candidate = client.generate_draft(SYSTEM_PROMPT, prompt)
            reason = validate_candidate(
                candidate["text"],
                candidate["reply_text"],
                product,
                history,
                now,
            )
            if reason is None:
                accepted = candidate
                if product is None:
                    used_pillars.add(pillar[0])
                break
            rejection_feedback = reason
            log.append(f"[重試 {attempt}/{max_retries}] 略過不合格草稿：{reason}")

        if accepted is None:
            log.append("[失敗] 連續重試仍無法產生不重複草稿，跳過本篇")
            continue

        pid = next_post_id(queue)
        prod_id = product["product_id"].strip() if product is not None else ""
        new_row = {col: "" for col in COLUMNS} | {
            "post_id": pid,
            "status": status,
            "text": accepted["text"],
            "reply_text": accepted["reply_text"],
            "product_id": prod_id,
            "created_at": now_iso(),
        }
        queue = pd.concat([queue, pd.DataFrame([new_row])], ignore_index=True)
        if not dry_run:
            save_queue(queue, queue_path)

        kind = f"分潤:{prod_id}" if prod_id else "純乾貨"
        preview = accepted["text"].replace("\n", " ")[:36]
        prefix = "[試產]" if dry_run else "[新增]"
        log.append(f"{prefix} {pid} ({status}, {kind})：{preview}…")
        created += 1

    log.append(f"完成：本次{'預覽' if dry_run else '新增'} {created}/{needed} 篇草稿")
    return log
