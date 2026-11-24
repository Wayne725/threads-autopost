from __future__ import annotations

import argparse
import os
import sys

from . import publisher
from .threads_api import ThreadsClient


def make_client() -> ThreadsClient:
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        sys.exit("缺少環境變數 THREADS_ACCESS_TOKEN")
    return ThreadsClient(token, user_id=os.environ.get("THREADS_USER_ID", "me"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="autopost", description="Threads 排程發文")
    parser.add_argument("--queue", default="posts.csv")
    sub = parser.add_subparsers(dest="command", required=True)

    pub = sub.add_parser("publish", help="發出下一篇 approved 草稿（預設試跑）")
    pub.add_argument("--live", action="store_true", help="真的發文")
    pub.add_argument("--max-posts", type=int, default=1)

    sub.add_parser("check", help="檢查草稿長度與重複，不改任何東西")
    sub.add_parser("whoami", help="確認 token 對應的帳號")
    sub.add_parser("refresh-token", help="延長 token 效期並印出新 token")

    args = parser.parse_args()

    if args.command == "publish":
        client = make_client() if args.live else None
        lines = publisher.run(args.queue, client, live=args.live, max_posts=args.max_posts)
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
