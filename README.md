# course-robot

從支點教育老師站讀取課表，濃縮成日曆事件後寫入 Google Calendar。
每天執行一次，把「今天之後」的事件整批重建。

## 這個工具做什麼

- 登入老師站，取得完整課表
- 把每個時段換算成實際授課時間（30 分鐘時段 → 25 分鐘；60 分鐘 → 50 分鐘）
- 同一學生連續的課程合併成一筆（兩堂 25 分鐘 → 一筆連續 50 分鐘）
- 依時間順序寫入指定的 Google Calendar

事件內容：

| 欄位 | 內容 |
|---|---|
| 標題 | `學生｜章節`（連續授課時取第一堂的章節） |
| 說明 | 年級、教材、解答（連續授課時教材與解答各兩份） |
| 時間 | 換算後的實際授課時間，`Asia/Taipei` |
| 可見性 | `private` |

## 運作方式

```
每天 00:00（Asia/Taipei）
  1. 登入老師站，抓取完整課表（含分頁驗證）
  2. 列出日曆上「今天 00:00 之後、且由本工具建立」的事件
  3. 全部刪除
  4. 依目前課表重新建立
  5. 重新列舉並比對筆數；不符就整輪重試（最多 3 次）
```

**沒有資料庫。** 事件 ID 交給 Google 產生，所以不需要記住任何狀態；
「由本工具建立」是靠事件的私有標記 `course_robot_managed` 辨識，
**你手動加到日曆的行程不會被刪除**。

安全規則：

- 只刪除「今天 00:00 之後」的事件 → 過去的紀錄一定保留
- 來源一筆都沒有時不執行刪除 → 避免把日曆清空
- 只碰帶管理標記的事件 → 手動建立的不受影響

## 安裝

```bash
uv sync
test -e .env || cp .env.example .env
chmod 600 .env
```

`.env`：

```dotenv
PEAK1_USERNAME=你的老師站帳號
PEAK1_PASSWORD=你的老師站密碼
PEAK1_BASE_URL=https://tutor.peak1.com.tw

GOOGLE_CALENDAR_ENABLED=true
GOOGLE_CALENDAR_ID=你的日曆ID@group.calendar.google.com
GOOGLE_OAUTH_CLIENT=credentials.json
GOOGLE_OAUTH_TOKEN=data/google-token.json

CALENDAR_SYNC_AT=00:00
```

> **`.env` 是唯一來源。** 程式以 `override=True` 載入，檔案內容一定勝過環境變數——
> 否則 shell 或容器裡的殘留變數會靜默蓋掉設定。

> 需要老師站帳密，是因為伺服器要在你不在電腦前時自己登入。
> 帳密只存在這台機器的 `.env`，不會進 Git、不會進 image。

## Google 授權

完整步驟見 `docs/Google授權設定.md`。重點：

1. GCP 專案啟用 **Google Calendar API**（換專案時最容易漏）
2. OAuth 同意畫面 Publishing status 必須是 **In production**，否則 refresh token 7 天後失效
3. Scope 只留 `https://www.googleapis.com/auth/calendar.events`
4. 授權與驗證：

```bash
./scripts/dev.sh calendar-auth --no-browser   # WSL 建議加 --no-browser
./scripts/dev.sh calendar-check               # 唯讀確認權限
```

## 使用

```bash
./scripts/dev.sh calendar-plan          # 預覽會刪除與新增哪些事件，不寫入
./scripts/dev.sh calendar-sync          # 實際重建
./scripts/dev.sh calendar-sync --dry-run
./scripts/dev.sh calendar-daemon --run-now   # 常駐，每天 CALENDAR_SYNC_AT 執行
```

| 指令 | 用途 |
|---|---|
| `calendar-auth` | 重新授權（token 失效時） |
| `calendar-check` | 唯讀確認 token 對日曆有權限 |
| `calendar-plan` | 預覽，不寫入 |
| `calendar-sync` | 執行一次重建 |
| `calendar-daemon` | 常駐，每天固定時間執行 |

## 測試

```bash
./scripts/dev.sh test
```

## 部署到 VPS

```bash
# 1. 上傳（.env、token 不進 Git，要另外傳）
scp .env your-user@your-vps:/opt/course-robot/.env
scp credentials.json your-user@your-vps:/opt/course-robot/credentials.json
scp data/google-token.json your-user@your-vps:/opt/course-robot/data/google-token.json

# 2. 設定權限（容器以 UID 10001 執行）
cd /opt/course-robot
sudo install -d -o 10001 -g 10001 data
sudo chmod 700 data
sudo chmod 600 .env data/google-token.json

# 3. 啟動
cd deploy/calendar
sudo docker compose up -d --build
sudo docker compose logs --tail=50 -f
```

容器啟動後會先跑一次，之後每天在 `CALENDAR_SYNC_AT` 執行。
**只需要這一個服務**，不需要資料庫、不需要對外開放任何連接埠。

日誌會顯示每次重建的結果，以及是否通過審查：

```
重建完成：刪除 45、新增 45；第 1 次通過審查
```

若連續失敗，會出現 `重試 3 次仍未通過審查` 與具體原因。

## 已知特性

- **事件 ID 每天改變**：因為整批重建、ID 由 Google 產生。日曆的同步與檢視不受影響，
  但如果你手動對某個事件加過提醒、顏色或備註，隔天重建時會消失。
- **事件裡沒有教室連結**：老師站回傳的 `class_url` 目前一律為空，
  且你實際使用時是直接看日曆，所以說明只保留年級、教材、解答。
- 連續授課的判定是「同一學生、同一天、時間相接」，不分年級。

## 文件

| 文件 | 內容 |
|---|---|
| `docs/架構設計.md` | 架構決策與演進過程 |
| `docs/實作清點.md` | 實作細節、踩過的坑與已知限制 |
| `docs/Google授權設定.md` | OAuth 完整步驟（含換帳號情境） |
| `docs/VPS上線清單.md` | 部署到 VPS 的逐步操作與疑難排解 |
