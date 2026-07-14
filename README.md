# 隨叫隨到 Discord Bot

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

[繁體中文](README.md) | [English](README.en.md)

以 Python 3.11 與 discord.py 開發的多功能 Discord Bot，整合伺服器管理、防護、新成員驗證、客服單、自訂互動面板、定時提醒、聊天摘要與語音轉文字。專案使用 SQLite 持久化資料，並提供繁體中文、簡體中文及英文介面。

## 主要功能

### 管理與自動化

- 互動式伺服器設定面板、公告頻道與成員人數頻道。
- 批次刪除訊息、發送公告、匯出聊天紀錄及刪除訊息紀錄。
- 自訂文字觸發詞、新成員歡迎訊息與定時提醒。
- 自動更新伺服器人數顯示。

### 防護

- 蜜罐頻道：刪除違規訊息，並依權限執行封禁。
- 防洗版：偵測跨頻道或單一頻道重複內容，執行禁言與刪除。
- 防炸群：偵測短時間內大量成員加入並發出警示。
- 新成員驗證系統：被動風險評分＋按鈕驗證，高風險成員轉真人審核（獨立私人頻道），啟用時可一次性放行既有成員並鎖定頻道發言權限。
- 使用 Google Safe Browsing API 與可自訂關鍵字黑名單檢查惡意連結。
- 圖片詐騙偵測：比對已知詐騙圖片的感知雜湊資料庫，並解碼圖片附件中的 QR code（解出的網址會併入相同的網址安全性檢查流程）。
- 白名單與 Discord 防護事件通知。
- 重要防護事件及處置結果寫入資料庫稽核紀錄與本機輪替日誌。

### 客服單與自訂面板

- 建立持久化客服單面板，支援開啟、關閉、刪除及匯出紀錄。
- 建立自訂 Embed、按鈕及表單面板。
- 支援身分組、頻道與審核流程；Bot 重啟後會重新註冊 View。

### AI

- 使用 Groq 產生聊天摘要。
- 使用 Groq 將 `ogg`、`m4a`、`mp3`、`wav`、`flac`、`aac` 轉為文字。
- 未設定 Groq API Key 時只停用 AI 功能，不影響其他模組。

聊天摘要：回覆摘要起點訊息並提及 Bot。語音轉文字：提及 Bot 並附加音訊，或回覆含音訊的訊息後提及 Bot。

## 系統需求

- Python 3.11，開發環境為 Python 3.11.9。
- Discord Bot Token。
- Groq API Key，僅 AI 功能需要。
- Google Safe Browsing API Key，僅連結檢查需要。

## 安裝

### Windows PowerShell

```powershell
git clone <repository-url>
cd At_Your_Service_Discord_Bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

### Linux

以下以 Ubuntu/Debian 為例。`libzbar0` 是 QR code 解碼套件 `pyzbar` 需要的系統函式庫。

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv python3.11-dev libzbar0
git clone <repository-url>
cd At_Your_Service_Discord_Bot
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example token.env
```

編輯 `token.env` 後啟動：

```bash
python bot.py
```

若發行版套件庫沒有 `python3.11`，請先用該發行版建議方式安裝 Python 3.11，再回到建立 venv 的步驟。

目前專案尚未提供套件鎖定檔，Python 套件會由 `pyproject.toml` 安裝。

## Discord 設定

1. 在 Discord Developer Portal 建立 Application 與 Bot。
2. 啟用 Server Members Intent 與 Message Content Intent。
3. 邀請 Bot 時加入 `bot` 與 `applications.commands` scopes。
4. 依啟用功能授予 View Channels、Send Messages、Read Message History、Manage Messages、Moderate Members、Ban Members、Manage Channels、Manage Roles、Attach Files 與 Embed Links。
5. 將 Bot 身分組移至需要管理的成員及身分組上方。

## 環境變數

Windows PowerShell：

```powershell
Copy-Item .env.example token.env
```

Linux：

```bash
cp .env.example token.env
```

編輯 `token.env`：

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
GOOGLE_SAFE_BROWSING_KEY=your_google_safe_browsing_key
```

| 變數 | 必要性 | 用途 |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | 必要 | 啟動 Discord Bot |
| `GROQ_API_KEY` | 選用 | 聊天摘要及語音轉文字 |
| `GOOGLE_SAFE_BROWSING_KEY` | 選用 | 惡意網址檢查 |
| `BACKUP_RCLONE_REMOTE` | 選用 | 資料庫備份異地同步到 Google 雲端硬碟的 rclone 遠端名稱，見〈資料庫備份〉 |

`token.env` 已由 `.gitignore` 排除，請勿提交。

## 應用程式設定

主要設定位於 `config/config.json`。

| 設定 | 說明 |
| --- | --- |
| `anti_spam.time_window_seconds` | 防洗版偵測時間窗，單位為秒 |
| `anti_spam.channel_threshold` | 相同內容跨頻道觸發門檻 |
| `anti_spam.same_channel_threshold` | 相同內容於單一頻道觸發門檻 |
| `anti_spam.timeout_hours` | 觸發後的禁言時數 |
| `anti_spam.cleanup_interval_minutes` | 歷史紀錄清理間隔 |
| `ai_settings.chat_summary_model` | Groq 聊天摘要模型 |
| `ai_settings.voice_transcribe_model` | Groq 語音轉文字模型 |
| `ai_settings.chat_history_limit` | 摘要最多讀取的歷史訊息數 |
| `people_counting.update_interval_minutes` | 成員人數更新間隔 |
| `anti_raid.join_window_seconds` | 防炸群偵測時間窗，單位為秒 |
| `anti_raid.join_threshold` | 時間窗內觸發警示的加入人數門檻 |
| `verification.new_account_days` | 視為「新帳號」的天數門檻（影響風險評分） |
| `verification.risk_threshold` | 風險分數達到此門檻即轉真人審核 |

修改模型前，請確認 Groq 帳號可存取該模型。

## 啟動

Windows PowerShell 或已啟用 venv 的 Linux shell：

```powershell
python bot.py
```

啟動時會載入所有 Cog 並同步全域 Slash Commands。Discord 可能需要一段時間才會在所有伺服器顯示新指令。

## Slash Commands

| 指令 | 權限 | 說明 |
| --- | --- | --- |
| `/server_setting` | 管理員 | 通用伺服器設定 |
| `/anti_fraud_setting` | 管理員 | 蜜罐、白名單、防洗版、連結檢查、防炸群及新成員驗證系統 |
| `/trigger_setting` | 管理員 | 管理文字觸發詞 |
| `/custom_panel` | 管理員 | 建立自訂互動面板 |
| `/welcome_setting` | 管理員 | 設定歡迎訊息 |
| `/warning_setting` | 管理員 | 管理定時提醒 |
| `/set_language` | 管理員 | 切換介面語言 |
| `/delete` | 管理員 | 刪除近期訊息，最多 100 則 |
| `/announcement` | 管理員 | 由 Bot 發送公告 |
| `/export_chat` | 一般使用者 | 匯出目前頻道聊天紀錄 |
| `/ticket` | 管理員 | 建立客服單面板 |

新成員驗證系統沒有獨立指令，透過 `/anti_fraud_setting` 面板內的選單設定與啟用。

連結檢查關鍵字黑名單管理、GDPR 稽核紀錄刪除與已知詐騙圖片雜湊資料庫不透過 Discord 指令開放，而是機器人擁有者在執行機器人的終端機輸入文字指令操作（`admin keyword list/add/remove`、`admin gdpr delete <user_id>`、`admin scamimage list/add/remove/sync`），機器人啟動時終端機會印出完整說明。路徑或關鍵字中間含空白時，請用雙引號包住，例如 `admin scamimage sync "C:\path\scam image"`。

## 多語言

支援 `zh-TW`、`zh-CN`、`en-US`。翻譯位於 `locales/languages.json`，管理員可透過 `/set_language` 或設定面板切換。

## 資料與日誌

所有持久化資料都存在 SQLite 資料庫 `data/bot.db`（WAL 模式），包含觸發詞、連結檢查關鍵字、稽核紀錄、驗證系統紀錄、客服單與面板、自訂面板、定時提醒、伺服器設定等。部分功能（客服單、自訂面板、定時提醒、伺服器設定）額外有記憶體快取層以加速讀取，寫入時會同步更新資料庫與快取。

| 路徑 | 內容 |
| --- | --- |
| `data/bot.db` | 主要資料庫（SQLite），機器人所有持久化資料 |
| `logs/bot.log` | 錯誤與重要事件日誌 |

日誌單檔上限 5 MB，最多保留 5 份備份，且已由 `.gitignore` 排除。日誌可能包含伺服器、頻道及使用者 ID，分享前應先移除不應公開的資訊。

SQLite 執行期檔案與舊版 JSON 資料檔皆已由 `.gitignore` 排除，不應提交至版控。同一資料目錄建議只執行一個 Bot 實例，避免多個程序同時寫入資料庫。資料庫備份見下一節。

## 資料庫備份

`scripts/backup_databases.py` 提供每日排程備份，搭配選用的 Google 雲端硬碟異地同步。

### 備份機制

- 用 SQLite 官方 Online Backup API（`Connection.backup()`）備份，不是單純複製檔案——`data/bot.db` 是 WAL 模式，直接複製檔案有機率拿到寫入中途的不一致狀態，`backup()` 保證即使機器人正在寫入也能拿到一致快照。
- 本地輸出在 `backups/bot_db/<日期>.db`，保留最近 7 天的每日備份，另外每月 1 號的備份永久保留、不會被輪替刪除。
- 執行紀錄寫進 `logs/backup.log`（跟 `logs/bot.log` 分開，避免排程執行時互相干擾輪替時機）；任何一項備份失敗，腳本會以非零結束碼結束，方便排程工具偵測失敗。

### 手動執行一次

```bash
python scripts/backup_databases.py
```

### 設定每日排程

Windows（工作排程器）：

```powershell
schtasks /create /tn "HoneypotBotBackup" /tr "python C:\path\to\repo\scripts\backup_databases.py" /sc daily /st 03:00
```

Linux（cron，`crontab -e`）：

```
0 3 * * * cd /path/to/repo && /path/to/venv/bin/python scripts/backup_databases.py
```

### 異地同步到 Google 雲端硬碟（選用，強烈建議設定）

只有本地備份防不了硬碟本身故障，異地同步才是真正的保護。步驟：

1. 安裝 [rclone](https://rclone.org/downloads/)。
2. 執行 `rclone config`，依序選擇：`n`（新遠端）→ 自訂名稱（例如 `gdrive`）→ storage 類型選 `Google Drive` → 其餘選項一路保持預設按 Enter → 「Use auto config?」選 `y`，會開啟瀏覽器登入並授權你的 Google 帳號。
3. 執行 `rclone lsd gdrive:` 確認能列出雲端硬碟裡的資料夾，代表授權成功。
4. 在 `token.env` 加入：

   ```dotenv
   BACKUP_RCLONE_REMOTE=gdrive:honeypot-bot-backups
   ```

5. 之後每次 `backup_databases.py` 執行，本地備份成功後會自動 `rclone copy` 整個 `backups/` 資料夾同步過去。**刻意用 `copy` 不用 `sync`**：`sync` 會把本地輪替刪掉的舊備份連雲端那份也砍掉，`copy` 只增量上傳、不刪除雲端既有檔案，異地保留多久由你自己在雲端硬碟裡管理。
6. 若同時在多台機器上跑（例如本機開發＋正式部署主機），每台機器都要各自跑一次 `rclone config`，或把設定好的 `rclone.conf` 設定檔安全地複製過去。

未設定 `BACKUP_RCLONE_REMOTE`，或機器上找不到 `rclone` 執行檔時，腳本只做本地備份，不會被當成失敗。

### 還原演練

備份沒驗證過等於沒有，建議定期實際演練一次：

1. 停止機器人。
2. 用要還原的備份檔覆蓋 `data/bot.db`，並刪除殘留的 `data/bot.db-wal`、`data/bot.db-shm`（若存在）。
3. 重新啟動機器人，確認資料完整。

## 專案結構

```text
honeypot-discord-bot/
|-- bot.py                 # 程式入口與擴充模組載入
|-- pyproject.toml         # Python 版本、依賴與開發工具設定
|-- core/                  # 設定、資料庫、i18n、logging 與生命週期
|-- features/              # 依功能分組的 Cog、Panel、Service 與 Repository
|-- hubs/                  # 跨功能設定入口與組合面板
|-- admin/                 # 本機終端機管理工具
|-- dev/                   # 本機開發工具（敏感檔案不進版控）
|-- config/config.json     # 全域設定
|-- data/                  # SQLite 持久化資料
|-- scripts/               # 排程/維運腳本（例如資料庫備份）
|-- locales/languages.json # 多語言文字
|-- .env.example           # 環境變數範例
|-- LICENSE                # GPL-3.0
|-- README.md              # 繁體中文文件
`-- README.en.md           # 英文文件
```

## 常見問題

### Slash Command 沒有出現

確認邀請時包含 `applications.commands`、啟動時沒有同步錯誤，並等待 Discord 完成全域同步。

### Bot 無法讀取訊息

啟用 Message Content Intent，並確認 Bot 具有 View Channels 與 Read Message History 權限。

### Bot 無法禁言、封禁或管理身分組

確認 Bot 具有對應權限，且其最高身分組高於目標。Bot 無法管理伺服器擁有者或角色高於自身的成員。

### AI 或連結檢查無法使用

確認對應 API Key 已寫入 `token.env`、模型可用，並查看終端機與 `logs/bot.log`。

## 安全注意事項

- 不要將 Token、API Key、日誌或私密匯出資料提交至 Git。
- 若密鑰曾提交至 Git，應立即撤銷並重新產生；只刪除檔案無法清除歷史。
- 建議定期更新依賴、備份資料並檢查 Bot 權限。

## 貢獻

歡迎提交 Issue 或 Pull Request。Pull Request 應符合以下要求：

- 可在 Python 3.11 執行。
- 不包含 Token、API Key、日誌或私密資料。
- 新增的使用者文字包含 `zh-TW`、`zh-CN`、`en-US` 翻譯。
- 已執行與變更範圍相稱的驗證。

## 授權

本專案使用 GNU General Public License v3.0，完整條款請參閱 `LICENSE`。

## 致謝

感謝 Linvin 提供蜜罐功能的想法與相關原始碼參考。


另外說實話現在AI真好用，本專案所有的註釋和commit都是AI寫的，真棒
~~除了邪惡claude速度慢tokens又給的少，有些事情還不如我自己做~~
