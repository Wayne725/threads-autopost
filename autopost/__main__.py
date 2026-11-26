from __future__ import annotations

import argparse
import os
import sys

from . import generator, publisher
from .gemini_api import DEFAULT_MODEL as DEFAULT_GEMINI_MODEL
from .gemini_api import GeminiClient
from .llm_api import OpenAICompatibleClient
from .threads_api import ThreadsClient


def make_client() -> ThreadsClient:
    token = (os.environ.get("THREADS_ACCESS_TOKEN") or "").strip()
    if not token:
        sys.exit("缺少環境變數 THREADS_ACCESS_TOKEN")
    user_id = (os.environ.get("THREADS_USER_ID") or "").strip() or "me"
    return ThreadsClient(token, user_id=user_id)


def make_llm_client(model: str | None = None, base_url: str | None = None) -> OpenAICompatibleClient | GeminiClient:
    llm_key = (os.environ.get("LLM_API_KEY") or "").strip()
    resolved_base_url = (base_url or os.environ.get("LLM_BASE_URL") or "").strip()
    if llm_key or resolved_base_url:
        if not llm_key:
            sys.exit("缺少環境變數 LLM_API_KEY（請填入高科 iAI 的 API Key）")
        if not resolved_base_url:
            sys.exit("缺少環境變數 LLM_BASE_URL（請填入高科 iAI 教學中心提供的 API 主機網址）")
        chosen_model = (model or os.environ.get("LLM_MODEL") or "").strip()
        if not chosen_model:
            sys.exit("缺少環境變數 LLM_MODEL（可先執行 `python -m autopost models` 查詢可用模型名稱）")
        return OpenAICompatibleClient(api_key=llm_key, base_url=resolved_base_url, model=chosen_model)

    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if gemini_key:
        chosen_model = (model or os.environ.get("GEMINI_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL
        return GeminiClient(gemini_key, model=chosen_model)

    sys.exit("缺少 LLM 設定：請設定 LLM_API_KEY + LLM_BASE_URL + LLM_MODEL（高科 iAI），或設定 GEMINI_API_KEY")


def make_openai_client(base_url: str | None = None) -> OpenAICompatibleClient:
    llm_key = (os.environ.get("LLM_API_KEY") or "").strip()
    resolved_base_url = (base_url or os.environ.get("LLM_BASE_URL") or "").strip()
    if not llm_key:
        sys.exit("缺少環境變數 LLM_API_KEY（請填入高科 iAI 的 API Key）")
    if not resolved_base_url:
        sys.exit("缺少環境變數 LLM_BASE_URL（請填入高科 iAI 教學中心提供的 API 主機網址）")
    return OpenAICompatibleClient(api_key=llm_key, base_url=resolved_base_url, model="")


def main() -> None:
    parser = argparse.ArgumentParser(prog="autopost", description="Threads 排程發文")
    parser.add_argument("--queue", default="posts.csv")
    sub = parser.add_subparsers(dest="command", required=True)

    pub = sub.add_parser("publish", help="發出下一篇 approved 草稿（預設試跑）")
    pub.add_argument("--live", action="store_true", help="真的發文")
    pub.add_argument("--max-posts", type=int, default=1)

    gen = sub.add_parser("generate", help="用高科 iAI（或相容 LLM API）自動補齊不重複草稿")
    gen.add_argument("--products", default="products.csv", help="分潤商品清單 CSV（預設 products.csv）")
    gen.add_argument("--min-queue", type=int, default=6, help="低於此篇數才自動補貨（預設 6）")
    gen.add_argument("--target-queue", type=int, default=10, help="補貨目標篇數（預設 10）")
    gen.add_argument("--count", type=int, default=None, help="強制指定本次生成篇數（忽略 min-queue）")
    gen.add_argument("--status", choices=["draft", "approved"], default="approved", help="生成後的狀態（預設 approved）")
    gen.add_argument("--ratio", type=int, default=3, help="每幾篇純乾貨搭配 1 篇分潤文（預設 3）")
    gen.add_argument("--base-url", default=None, help="OpenAI 相容 API 主機網址（如高科 iAI Endpoint）")
    gen.add_argument("--model", default=None, help="模型名稱（覆蓋 LLM_MODEL / GEMINI_MODEL）")
    gen.add_argument("--dry-run", action="store_true", help="試產預覽，不寫入 posts.csv")

    models_cmd = sub.add_parser("models", help="列出高科 iAI（OpenAI 相容 API）目前可用的模型名稱")
    models_cmd.add_argument("--base-url", default=None, help="OpenAI 相容 API 主機網址")

    sub.add_parser("check", help="檢查草稿長度與重複，不改任何東西")
    sub.add_parser("whoami", help="確認 token 對應的帳號")
    sub.add_parser("refresh-token", help="延長 token 效期並印出新 token")

    args = parser.parse_args()

    if args.command == "publish":
        client = make_client() if args.live else None
        lines = publisher.run(args.queue, client, live=args.live, max_posts=args.max_posts)
    elif args.command == "generate":
        llm = make_llm_client(model=args.model, base_url=args.base_url)
        lines = generator.run(
            queue_path=args.queue,
            client=llm,
            products_path=args.products,
            min_queue=args.min_queue,
            target_queue=args.target_queue,
            count=args.count,
            status=args.status,
            content_per_affiliate=args.ratio,
            dry_run=args.dry_run,
        )
    elif args.command == "models":
        model_ids = make_openai_client(base_url=args.base_url).list_models()
        lines = model_ids or ["（查無可用模型）"]
    elif args.command == "check":
        lines = publisher.check(args.queue)
    elif args.command == "whoami":
        me = make_client().me()
        lines = [f"@{me['username']}（user_id={me['id']}）"]
    else:
        result = make_client().refresh_token()
        lines = [f"新 token 有效 {int(result['expires_in']) // 86400} 天：", result["access_token"]]

    print("\n".join(lines))


if __name__ == "__main__":
    main()
