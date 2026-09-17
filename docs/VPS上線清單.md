# VPS 上線清單

照著由上往下做即可。每步驟都有「驗證方式」，通過再做下一步。

---

## 0. 前置確認（本機）

```bash
cd ~/repos/personal/projects/course-robot
./scripts/dev.sh test                # 應顯示 OK
./scripts/dev.sh calendar-check      # 應顯示「權限確認成功」
```

`.env` 內容確認：

```dotenv
PEAK1_USERNAME=…                     # 老師站帳號
PEAK1_PASSWORD=…
PEAK1_BASE_URL=https://tutor.peak1.com.tw
GOOGLE_CALENDAR_ENABLED=true
GOOGLE_CALENDAR_ID=…@group.calendar.google.com   # 不可為 primary
GOOGLE_OAUTH_CLIENT=credentials.json
GOOGLE_OAUTH_TOKEN=data/google-token.json
CALENDAR_SYNC_AT=00:00
```

**需要上傳的三個檔案**（都不進 Git）：

| 檔案 | 說明 |
|---|---|
| `.env` | 老師站帳密與 Google 設定 |
| `credentials.json` | OAuth client |
| `data/google-token.json` | refresh token（授權的產物） |

---

## 1. VPS 環境

```bash
# 確認為 Linux x86_64 且有 Docker
uname -m && docker --version && docker compose version
```

若尚未安裝 Docker：

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # 重新登入後生效
```

---

## 2. 取得程式碼

```bash
sudo mkdir -p /opt/course-robot
sudo chown "$USER":"$USER" /opt/course-robot

# 方式 A：用 Git（確認 .env、token 未被追蹤）
git clone <你的 repo 位址> /opt/course-robot
cd /opt/course-robot && git log --oneline -1

# 方式 B：直接上傳（沒有遠端 repo 時）
rsync -av --exclude '.venv' --exclude '.git' --exclude 'data' \
  ./ your-user@your-vps:/opt/course-robot/
```

驗證：`/opt/course-robot/deploy/calendar/compose.yaml` 存在。

---

## 3. 上傳機密檔案

在**本機**執行：

```bash
cd ~/repos/personal/projects/course-robot
scp .env your-user@your-vps:/opt/course-robot/.env
scp credentials.json your-user@your-vps:/opt/course-robot/credentials.json
scp data/google-token.json your-user@your-vps:/opt/course-robot/data/google-token.json
```

在 **VPS** 上設定權限（容器以 UID 10001 執行）：

```bash
cd /opt/course-robot
sudo install -d -o 10001 -g 10001 data
sudo chmod 700 data
sudo chmod 600 .env credentials.json data/google-token.json
```

驗證：

```bash
ls -la .env credentials.json data/google-token.json
# 三個都應該是 -rw------- 或 -rw-r--r--（只有擁有者可讀寫）
```

---

## 4. 啟動

```bash
cd /opt/course-robot/deploy/calendar
sudo docker compose up -d --build
```

驗證（三個都要通過）：

```bash
# a) 容器在跑
sudo docker compose ps
# 狀態應為 Up (healthy) 或 Up

# b) 啟動時的那次同步成功
sudo docker compose logs --tail=30
# 應看到：重建完成：刪除 N、新增 M；第 1 次通過審查

# c) 下一次執行時間正確
sudo docker compose logs | grep "下一次執行"
# 應為明天的 00:00（Asia/Taipei）
```

---

## 5. 上線後確認（重要）

**開一次 Google Calendar**，確認：

- [ ] 今天之後的課都在，時間是 25／50 分鐘
- [ ] 連續授課合併成一筆（例如 20:00–20:50）
- [ ] 事件說明只有「年級／教材／解答」
- [ ] 你手動加的私人行程**沒有被動到**

**隔天再確認一次**（這是最關鍵的一次，驗證每日排程真的會跑）：

```bash
sudo docker compose logs --tail=20
# 應出現新的一次「重建完成」
```

---

## 6. 之後的日常維運

| 情況 | 處理 |
|---|---|
| 想立刻同步（不等 00:00） | `sudo docker compose exec calendar course-robot calendar-sync` |
| 想看會做什麼（不寫入） | `sudo docker compose exec calendar course-robot calendar-plan` |
| token 失效（日誌出現 `refresh token` 相關錯誤） | 本機重跑 `./scripts/dev.sh calendar-auth --no-browser`，重新 `scp data/google-token.json`，再 `docker compose restart` |
| 改課表同步時間 | 改 `.env` 的 `CALENDAR_SYNC_AT` 後 `docker compose up -d` |
| 更新程式 | `git pull` 後 `sudo docker compose up -d --build` |
| 看日誌 | `sudo docker compose logs -f`（已設定輪替：單檔 10MB、保留 3 份） |

---

## 7. 更新前後的檢查

每次改動後、重啟前，先在本機跑：

```bash
./scripts/dev.sh test
```

然後在 VPS 上先用預覽確認，再正式套用：

```bash
sudo docker compose exec calendar course-robot calendar-plan
```

---

## 疑難排解

| 症狀 | 原因與處理 |
|---|---|
| `找不到 Google token` | `data/google-token.json` 沒上傳或權限不對（容器讀不到） |
| `無法寫入 data/` | 目錄擁有者不是 10001：`sudo chown -R 10001:10001 data` |
| `HTTP 404` 且日曆不存在 | `.env` 的 `GOOGLE_CALENDAR_ID` 填錯 |
| `HTTP 403` 且提到 API 未啟用 | GCP 專案沒開 Google Calendar API |
| `重試 3 次仍未通過審查` | 看同一行後面附的原因；多半是網路不穩，下一輪會自己好 |
| 事件全部消失 | **不應該發生**（原始碼有保護）。若真的發生，日誌會有線索，先別重啟，保留日誌 |
| 日曆沒更新，但日誌顯示成功 | 確認 `.env` 的 `GOOGLE_CALENDAR_ID` 是你以為的那本日曆 |

---

## 這個部署不需要的東西

- 不需要資料庫（無狀態）
- 不需要對外開放任何連接埠
- 不需要 Nginx／Caddy／反向代理
- 不需要 systemd timer（容器自己排程）
