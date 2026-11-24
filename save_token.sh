#!/bin/zsh
# 把 token 存進 .env（不會顯示在畫面上，.env 不會進 git）
cd "${0:A:h}"
read -rs "token?Paste Threads token (hidden), then Enter: "
echo
[[ -z "$token" ]] && { echo "沒有輸入，未儲存"; exit 1; }
printf 'THREADS_ACCESS_TOKEN=%s\n' "$token" > .env
chmod 600 .env
echo "已存到 .env（長度 ${#token}）"
