# Threads 排程發文

從 `posts.csv` 依序發出你核准的貼文（支援兩段式串文），用 GitHub Actions 每天 12:00、21:00 自動執行。

## 草稿流程

`posts.csv` 每一列是一篇貼文：

| 欄位 | 說明 |
|---|---|
| `post_id` | 唯一編號，例如 `p003` |
| `status` | `draft`（草稿）→ 你改成 `approved` 才會發 → `publishing` → `posted`；被擋下的變 `rejected` |
| `text` | 第一段，最多 500 字 |
| `reply_text` | 第二段（接在第一段下面的串文），可留空 |
| `product_id` | 蝦皮商品 ID，同商品 14 天內不重推，可留空 |
| `note` | 被擋下的原因（程式自動填） |
| 其餘欄位 | 程式自動填，不要手動改 |

每次排程只發 **1 篇**，照檔案順序挑第一篇 `approved`。

```bash
.venv/bin/python -m autopost check            # 檢查長度與重複
.venv/bin/python -m autopost publish          # 試跑：只顯示會發什麼
.venv/bin/python -m autopost publish --live   # 真的發
```

> 排程跑完會把狀態 commit 回 repo，所以**改 `posts.csv` 前先 `git pull`**，避免衝突。

## 不會重複發的機制

1. **狀態**：只發 `approved`，發完立刻改 `posted`；每一步都先存檔。
2. **中斷補救**：發文前先標 `publishing`。若上次中途當掉，下次會先查 Threads 上是否已發出，有就補記錄，沒有才補發；第二段只認掛在同一篇底下的回覆。
3. **同時執行**：GitHub Actions 設 `concurrency`，同一時間只有一個發文工作。
4. **內容重複**：去掉空白、大小寫、網址後完全相同 → 擋；與最近 30 篇相似度 > 80% → 擋；同商品 14 天內推過 → 擋。

## 第一次設定

### 1. 建立 Meta 應用程式
1. 到 [Meta for Developers](https://developers.facebook.com/apps/) 建立應用程式，使用情境選 **Access the Threads API**。
2. 在使用情境的權限裡加上 `threads_basic`、`threads_content_publish`、`threads_read_replies`。
3. **應用程式角色 → Threads 測試人員**，加入你的 Threads 帳號。
4. 手機 Threads App：**設定 → 帳號 → 網站權限 → 邀請**，接受邀請。
5. 回到後台用 **User Token Generator** 產生你帳號的 token（長效 token，60 天）。

只發自己帳號不需要送審。Meta 後台介面常改，找不到選項就搜尋「Threads API get started」。

### 2. 存 token 並確認
```bash
zsh save_token.sh
```
```bash
set -a && source .env && set +a && .venv/bin/python -m autopost whoami
```

> Facebook 若擋下「不常用的裝置」，開發者註冊改在平常用的手機瀏覽器完成，之後電腦就能進後台。

### 3. 放上 GitHub
1. 建立 **private** repo，把這個資料夾推上去。
2. **Settings → Secrets and variables → Actions**：
   - Secrets：`THREADS_ACCESS_TOKEN`（必填）、`THREADS_USER_ID`（選填，預設 `me`）
   - Variables：`THREADS_LIVE` = `true`（沒設就只試跑，不會真的發）
3. **Actions** 分頁可以手動按 *Run workflow* 測試。

### 4. 每 50 天更新 token
token 60 天到期，過期排程會失敗（GitHub 會寄信通知）。到期前執行：
```bash
THREADS_ACCESS_TOKEN=舊token .venv/bin/python -m autopost refresh-token
```
把印出的新 token 貼回 GitHub Secret。token 要發出滿 24 小時才能更新。

## 本機開發
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```
