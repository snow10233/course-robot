# course-robot

從支點教育老師站的 JSON API 讀取課表，將最後一次成功取得的完整快照寫入 SQLite，並可將差異增量同步到 Google Calendar。Web 與 CSV 報表保留為本機除錯用的選配輸出，不是部署必要元件。

## Google Calendar 本機設定

程式只要求 `https://www.googleapis.com/auth/calendar.events` 權限。建議先在 Google Calendar 手動建立一個獨立的「支點課程」日曆，再把該日曆的 Calendar ID 填入 `.env`；也可以暫時使用 `primary`。

1. 在 Google Cloud Console 建立或選擇專案，啟用 Google Calendar API。
2. 設定 Google Auth Platform 的同意畫面；個人 Google 帳號選 External，並把自己的帳號加入 Test users。
3. 建立 Application type 為 Desktop app 的 OAuth Client，下載 JSON 並保存成專案根目錄的 `credentials.json`。
4. 安裝相依套件並進行一次本機授權：

```bash
uv sync
uv run course-robot calendar-auth
```

注意：External 應用若維持 Testing 狀態，Google 核發的 refresh token 會在 7 天後失效。短期測試可以先保持 Testing；準備放上 VPS 長期執行前，應將 Publishing status 改成 In production，再重新執行一次 `calendar-auth`。個人用途少於 100 位已知使用者通常不需要完成正式驗證，但授權時仍可能看到 unverified app 提示。

瀏覽器授權成功後，refresh token 會保存在 `data/google-token.json`。`credentials.json` 與 token 都已加入 `.gitignore`，不可提交或分享。

可以先做唯讀權限檢查，不會顯示或修改任何 Calendar 事件：

```bash
uv run course-robot calendar-check
```

第一次正式寫入前，先預覽異動：

```bash
uv run course-robot calendar-plan
```

這個指令會重新抓取支點課表並顯示預計新增、修改、刪除的數量，但不會修改 SQLite 或 Google Calendar。確認後，執行：

```bash
uv run course-robot sync --calendar
```

若要讓往後每次 `sync` 都同步 Calendar，在 `.env` 設定：

```dotenv
GOOGLE_CALENDAR_ENABLED=true
GOOGLE_CALENDAR_ID=你的 Calendar ID
GOOGLE_OAUTH_CLIENT=credentials.json
GOOGLE_OAUTH_TOKEN=data/google-token.json
```

Calendar 事件標題格式為「學生｜章節」。教材、解答及線上教室 URL 會放在事件說明中；事件設為 private。程式使用穩定的 Google event ID、私有 extended property 與 SQLite mapping，重試不會重複建立事件。只有 Calendar 全部異動成功後，SQLite 才會換成新快照。

Web 課表省略固定為數學的科目欄位。章節名稱會直接連到老師站標示為主要教材的 PDF（`main=Y`、`class_type=review`），右側的「（解）」則將「解」字連到主要解答 PDF（`main=Y`、`class_type=material_ans`）。缺少任一網址時，對應連結會省略或改成純文字。

前後端資料流刻意分開：Python 從 SQLite 產生 `schedule.json`，`schedule.html` 只保留固定版型，`schedule.js` 再透過 `fetch()` 取得 JSON，使用 DOM API 建立課程列與連結。課程資料不會寫死在 HTML 裡。

目前確認的公開前端呼叫流程：

- 登入：`POST /api/teacher/login`
- 課表：`GET /api/teacher/daily-schedule`
- 認證：`Authorization: Bearer ...`

程式不會解析 Gmail，也不會把帳號、密碼、Cookie 或 token 寫入資料庫。

## 安裝

### WSL 開發（從 Windows 搬遷）

在 WSL 終端使用 Linux 版 `uv`；本專案以 `.python-version` 指定 Python 3.12，與 Docker 的 Python 次版本一致。環境初始化會依 `uv.lock` 安裝依賴，需要能連線到套件來源。

```bash
cd ~/repos/personal/projects/course-robot
bash scripts/setup-wsl.sh
./scripts/dev.sh test
./scripts/dev.sh --help
```

初始化腳本會保留現有 `.env`、資料庫及 Google token；若找到 Windows 的 `.venv/Scripts`，會先將舊環境改名備份，再建立 Linux `.venv`。請勿沿用 Windows 的虛擬環境或 `docs/tmp/` 中的暫存套件。Shell 腳本統一使用 LF 換行，初始化時也會補上執行權限。

確認 `.env` 內的檔案路徑使用 `data/course-robot.db` 這類相對路徑，或 WSL 絕對路徑；不要使用 `C:\\...`。`scripts/dev.sh` 可以從任意工作目錄呼叫，它會先切換到專案根目錄再載入設定。

常用開發指令：

```bash
./scripts/dev.sh report          # 從現有 SQLite 產生報表
./scripts/dev.sh serve 8000      # 在 Windows 瀏覽器開啟 http://localhost:8000/schedule.html
./scripts/dev.sh calendar-plan   # 連線抓取課表，預覽 Calendar 異動
./scripts/run-sync.sh --no-calendar  # 抓取課表並寫入本機，紀錄存於 logs/course-robot.log
```

WSL 無法自動開啟瀏覽器時，執行 `./scripts/dev.sh calendar-auth --no-browser`，保持終端執行，將顯示的授權網址貼到 Windows 瀏覽器完成授權。回呼使用 localhost；若 Windows 無法連回 WSL，需檢查 WSL localhost 轉送設定。已搬入且仍有效的 `data/google-token.json` 可繼續使用。

WSL 日常開發不需要 Docker 或 systemd。`deploy/systemd/` 是 `/opt/course-robot` 伺服器用範本，不能直接套用到個人 WSL 路徑。Windows 的工作排程也不會隨目錄搬入 WSL；需要背景同步時再依下方 Docker 或 Linux 部署流程設定。

複製專案時也需保留完整 `.git` 才能延續版本歷史；若 `git status` 顯示不是 repository，請從原來源補回 Git 資料或重新 clone，並保留目前修改與私人設定檔。

### 一般 Linux 安裝

```bash
cd course-robot
uv sync --frozen
test -e .env || cp .env.example .env
chmod 600 .env
chmod +x scripts/run-sync.sh
```

編輯 `.env`，填入老師站帳號及密碼：

```dotenv
PEAK1_USERNAME=你的帳號
PEAK1_PASSWORD=你的密碼
```

`.env` 已列入 `.gitignore`，但仍是伺服器上的明文檔案，權限應保持為 `600`，請不要分享或上傳。

## 使用

初始化資料庫及空白報表：

```bash
uv run course-robot init
```

抓取完整課表、比對異動並更新報表：

```bash
uv run course-robot sync
```

只根據現有 SQLite 重新產生報表：

```bash
uv run course-robot report
```

預設輸出：

- `data/course-robot.db`
- `output/schedule.html`
- `output/schedule.json`
- `output/schedule.js`
- `output/schedule.css`
- `output/schedule.csv`

瀏覽器基於安全限制，通常不允許直接從 `file://` 頁面讀取旁邊的 JSON。需要在專案目錄啟動一個靜態 HTTP 伺服器預覽：

```bash
uv run python -m http.server 8000 --directory output
```

接著開啟 `http://127.0.0.1:8000/schedule.html`。部署到 Linux 時，可直接讓 Nginx、Caddy 或其他 Web server 提供 `output/` 目錄。

每次同步時，程式會把新資料與 SQLite 中的上一份成功快照依 `class_id` 比對，計算新增、修改、刪除及未變項目，接著把 SQLite 與報表更新成最新完整快照。差異只供當次處理及未來增量更新 Google Calendar 使用，不保存課程異動紀錄或歷史快照。若登入失敗、API 分頁不完整或欄位無法解析，上一份成功課表會保持不動。

## Docker 背景同步

Compose 只執行 `worker`，不開放連接埠。容器啟動後立即執行一次 `sync --calendar`，之後預設每 900 秒（15 分鐘）同步。HTML 與 CSV 報表在容器內停用。Google OAuth 所需的公開靜態頁面另外部署到 Cloudflare Workers，不由這個容器提供。

安全與持久化設計：

- `.env` 透過 Compose 的 `env_file` 在執行時注入，不會 COPY 進 image。
- `.dockerignore` 排除 `.env`、`credentials.json`、`data/`、token、SQLite、測試及本機輸出。
- 容器使用 UID/GID `10001` 的非 root 使用者、唯讀 root filesystem、移除所有 Linux capabilities，且設定 `no-new-privileges`。
- `data/` 綁定到 `/app/data`，保存 SQLite 與可自動更新的 Google OAuth token。
- 健康檢查會確認 SQLite 的最後成功同步時間在一小時內。
- 同步紀錄輸出到 Docker logs，單檔最多 10 MB、保留 3 份。

### 本機建置與檢查

目前 `.env` 裡的 `GOOGLE_CALENDAR_ENABLED` 可以維持 `false`；`compose.yaml` 只在容器內強制設成 `true`。先確認以下檔案存在：

```text
.env
data/google-token.json
data/course-robot.db
```

建置並啟動：

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 -f worker
```

停止或重新啟動：

```bash
docker compose stop
docker compose restart worker
docker compose down
```

`down` 只移除容器與 Compose network；目前使用 bind mount，因此不會刪除主機上的 `data/`。

## Cloudflare Workers OAuth 靜態頁面

`deploy/oauth-site/` 包含三個不讀取任何私人資料的公開頁面：

```text
https://course.zhidian.snowbox.dev/
https://course.zhidian.snowbox.dev/privacy/
https://course.zhidian.snowbox.dev/terms/
```

頁面透過 Workers Static Assets 提供，不需要 VPS、Caddy、固定 IP 或自行管理 HTTPS。第一次部署前先登入 Cloudflare：

```bash
npx wrangler login
npx wrangler deploy --config deploy/wrangler.jsonc
```

`deploy/wrangler.jsonc` 會將 `course.zhidian.snowbox.dev` 設為 Worker Custom Domain。Cloudflare 會自動建立所需 DNS 紀錄並核發 HTTPS 憑證，因此不要另外建立同名的 `A` 或 `CNAME` 紀錄；若已經建立，請先移除衝突紀錄。

部署後確認三個網址皆可在未登入狀態下開啟。這些頁面是 OAuth Production 的持續性公開資訊，驗證完成後也應保留部署並維持內容正確。

### 部署到 Linux VPS

將專案程式上傳到 VPS。因為 `.env` 與 `data/` 不應進 Git，請另外用安全方式傳送：

```bash
scp .env your-user@your-vps:/opt/course-robot/.env
scp data/google-token.json your-user@your-vps:/opt/course-robot/data/google-token.json
scp data/course-robot.db your-user@your-vps:/opt/course-robot/data/course-robot.db
```

在 VPS 上設定權限後啟動：

```bash
cd /opt/course-robot
sudo chown -R 10001:10001 data
sudo chmod 700 data
sudo chmod 600 .env data/google-token.json data/course-robot.db
sudo docker compose up -d --build
sudo docker compose ps
sudo docker compose logs --tail=100 worker
```

若不複製現有 SQLite，只傳 token 也能重建 mapping；穩定 Google event ID 會避免重複事件，但第一次會對全部課程執行一次 upsert，因此建議一併傳送目前的 `course-robot.db`。

同步間隔與健康檢查上限可在 `.env` 調整：

```dotenv
SYNC_INTERVAL_SECONDS=900
HEALTHCHECK_MAX_AGE_SECONDS=3600
```

`SYNC_INTERVAL_SECONDS` 不可低於 60。若 OAuth 應用仍是 External／Testing，refresh token 會在 7 天後失效；正式放到 VPS 前，請先將 Publishing status 改為 In production，並在本機重新執行 `calendar-auth` 後再上傳新 token。

### 設定 Google OAuth

先在 Google Search Console 以 DNS TXT 驗證 `snowbox.dev` 的 Domain property，接著在 Google Auth Platform 填入：

```text
App name: Snowbox Course Sync
Application home page: https://course.zhidian.snowbox.dev/
Application privacy policy: https://course.zhidian.snowbox.dev/privacy/
Application terms of service: https://course.zhidian.snowbox.dev/terms/
Authorized domain: snowbox.dev
Developer contact: sung951023@gmail.com
```

Data Access 只保留程式實際使用的 scope：

```text
https://www.googleapis.com/auth/calendar.events
```

頁面與 HTTPS 可公開存取後，到 Audience 將 Publishing status 切換為 `In production`。個人用途可以不提交完整資料存取驗證，但授權時仍可能出現 unverified app 警告。切換完成後，務必重新執行 `uv run course-robot calendar-auth`，再把新產生的 `data/google-token.json` 安全地上傳到 VPS。

舊的 `scripts/run-sync.sh` 與 `deploy/systemd/` 保留作為不使用 Docker 時的替代方案；Docker 部署不需要安裝 systemd timer。

## 測試

```bash
uv run python -m unittest discover -s tests -v
```
