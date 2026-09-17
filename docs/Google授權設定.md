# Google OAuth 重新授權操作手冊

目的：重新產生 `data/google-token.json`（目前這份已失效），並讓 VPS 能長期無人值守執行。
適用情境：**更換 Google 帳號**後第一次設定。
最後更新：2026-09-16

---

## 0. 先確認目前的三個問題

| 項目 | 檢查結果 | 說明 |
|---|---|---|
| **refresh token 已失效** | ✗ 實測 token endpoint 回 `400 invalid_grant` | 必須重新授權，沒有其他補救方式 |
| **`credentials.json` 與 token 的 client 不一致** | ✗ client_id 不同（credentials 是 `466621488889-…`，token 是 `778166335351-…`） | 授權前要決定用哪一個；建議重做一份 |
| **OAuth 公開頁面 DNS 不存在** | ✗ `course.zhidian.snowbox.dev` 無法解析（但 `snowbox.dev` 正常） | 需重新部署 Cloudflare 靜態頁，否則之後切 In production 會被擋 |

---

## 1. 建立／確認 OAuth Client（Desktop app）

1. 用**新的 Google 帳號**登入 <https://console.cloud.google.com/>
2. 上方專案選擇器 → 選 `course-zhidian`（既有專案）
   - 若已不存在或看不到 → 建立新專案，名稱例如 `course-zhidian`
3. 左側選單 → **API 和服務** → **已啟用的 API 和服務**
   - 確認 **Google Calendar API** 已啟用；沒有的話點「啟用 API 和服務」搜尋後啟用
4. 左側 → **API 和服務** → **憑證**
5. 點 **+ 建立憑證** → **OAuth 用戶端 ID**
   - 應用程式類型：**電腦版應用程式（Desktop app）**
   - 名稱：`course-robot desktop`
   - 建立後在彈出視窗點 **下載 JSON**
6. 把下載的檔案覆蓋到專案根目錄：

```bash
cd ~/repos/personal/projects/course-robot
# 把下載的檔案（通常在 ~/Downloads/client_secret_*.json）複製過來
cp ~/Downloads/client_secret_XXXX.json credentials.json
python3 -c "import json;d=json.load(open('credentials.json'));print('project:',d['installed']['project_id']);print('client_id:',d['installed']['client_id'][:20]+'…')"
```

> **為什麼不用既有的 `credentials.json`**：它的 client 與目前 token 不一致，來源不明。既然要換帳號，直接建一份新的最乾淨，避免之後出現 `invalid_client` 或 `unauthorized_client` 這類難查的錯誤。

---

## 2. 設定 OAuth 同意畫面

左側 → **API 和服務** → **OAuth 同意畫面**

### 2.1 品牌資訊（Audience / Branding）

| 欄位 | 值 |
|---|---|
| App name | `Snowbox Course Sync` |
| User support email | 你的新 Google 帳號 |
| Application home page | `https://course.zhidian.snowbox.dev/` |
| Application privacy policy | `https://course.zhidian.snowbox.dev/privacy/` |
| Application terms of service | `https://course.zhidian.snowbox.dev/terms/` |
| Authorized domain | `snowbox.dev` |
| Developer contact | 你的新 Google 帳號 |

> 這三個網址目前**無法解析**，必須先完成 §3 的部署，否則這一步存不進去。

### 2.2 資料存取範圍（Data Access）

只保留**一個** scope：

```
https://www.googleapis.com/auth/calendar.events
```

> 不要加 `calendar` 或 `calendar.readonly`。授權範圍越小，之後的驗證要求越少。

### 2.3 發布狀態

- **Audience** 頁面 → Publishing status → **Publish app / In production**

> **一定要在授權前切到 In production。** 維持 Testing 的話，refresh token **7 天後就會失效**，VPS 會每週停一次。

---

## 3. 重新部署 OAuth 公開頁面（DNS 已失效）

這三個頁面是 Google 驗證時的必查項目，也是「In production」的持續性要求。

```bash
cd ~/repos/personal/projects/course-robot
npx wrangler login          # 用你的 Cloudflare 帳號登入
npx wrangler deploy --config deploy/wrangler.jsonc
```

部署後驗證三個網址（應回 HTTP 200）：

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://course.zhidian.snowbox.dev/
curl -s -o /dev/null -w "%{http_code}\n" https://course.zhidian.snowbox.dev/privacy/
curl -s -o /dev/null -w "%{http_code}\n" https://course.zhidian.snowbox.dev/terms/
```

若仍是失敗：

- 到 Cloudflare Dashboard → **DNS** 檢查是否已有同名 `A` 或 `CNAME` 紀錄——**有的話要刪掉**，`wrangler.jsonc` 的 `custom_domain: true` 會自己建立正確的紀錄。
- 確認 Worker 名稱與 `wrangler.jsonc` 的 `name` 一致（`snowbox-course-sync-site`）。
- 用 `npx wrangler deploy --config deploy/wrangler.jsonc --dry-run` 先檢查設定是否被正確解析。

---

## 4. 本機授權，產生 token

### 4.1 執行授權

```bash
cd ~/repos/personal/projects/course-robot
./scripts/dev.sh calendar-auth --no-browser
```

- **WSL 環境建議加 `--no-browser`**：終端會印出一條授權網址，複製到 Windows 瀏覽器開啟。
- 一般 Linux／有桌面環境則可省略，會自動開啟瀏覽器。
- **保持終端開著**，授權完成後本機的 `localhost` 回呼會把結果傳回來。

### 4.2 授權畫面會看到什麼

1. 選擇**新的 Google 帳號**
2. 「Google 尚未驗證這個應用程式」→ 點 **進階** → **前往 Snowbox Course Sync（不安全）**
   - 這是正常的：個人用途不必送完整驗證，只要 scope 少、且網域已驗證，通常不會被擋
3. 勾選權限 → **繼續**
4. 看到「驗證完成」即可關閉瀏覽器，終端機會顯示：

```
Google Calendar 授權完成；token 已保存至 data/google-token.json
```

### 4.3 若卡住

| 症狀 | 原因與處理 |
|---|---|
| 瀏覽器顯示「無法連線至 localhost」 | WSL 的 localhost 轉送問題；改用 Windows 終端執行，或確認 WSL 網路設定 |
| `redirect_uri_mismatch` | Desktop app client 的 `redirect_uris` 應為 `http://localhost`；重新下載 `credentials.json` |
| `invalid_client` | `credentials.json` 與專案不符，重做 §1 |
| `access_denied` | 授權時按了拒絕，重新執行即可 |
| 一直停在等待 | 授權流程有時限，取消後重跑 |

---

## 5. 驗證 token 可用

```bash
# 1) 確認 token 檔內容正確
python3 -c "
import json; d=json.load(open('data/google-token.json'))
print('client_id :', d['client_id'][:20]+'…')
print('scopes    :', d['scopes'])
print('有 refresh_token:', bool(d.get('refresh_token')))
"

# 2) 實測 refresh 是否有效（最重要，這一步能證明不會 7 天後失效）
python3 - <<'EOF'
import json, httpx
d = json.load(open('data/google-token.json'))
r = httpx.post(d['token_uri'], data={
    'client_id': d['client_id'], 'client_secret': d['client_secret'],
    'refresh_token': d['refresh_token'], 'grant_type': 'refresh_token',
}, timeout=20)
print('HTTP', r.status_code, '→', '有效' if r.status_code == 200 else r.text[:200])
EOF
```

應該看到 `HTTP 200 → 有效`。若還是 `invalid_grant`，代表授權沒成功或仍在 Testing 狀態。

---

## 6. 選擇目標日曆

1. 在新的 Google 帳號裡，Google Calendar → 左側「其他日曆」→ **+** → **建立新日曆**
2. 名稱建議 `支點課程`
3. 建立後 → 日曆設定 → 往下找 **日曆 ID**（形如 `xxxxx@group.calendar.google.com`）
4. 填入 `.env`：

```dotenv
GOOGLE_CALENDAR_ID=你的新日曆ID
GOOGLE_CALENDAR_ENABLED=true
```

> ⚠️ **不要用 `primary`**。`.env.example` 的預設值是 `primary`，忘了改的話，程式會把課程事件建到你**主日曆**裡。

---

## 6.5 【常見情境】OAuth client 在主帳號，日曆在測試帳號

這是目前實際的設定：**GCP 專案與 OAuth client 屬於主帳號，但日曆屬於測試帳號。**

**先講結論：可行，而且不需要為測試帳號再建一個 client。** OAuth client 決定的是「哪個應用程式在要求權限」，授權時登入哪個 Google 帳號是另一回事——任何帳號都可以授權這個 client。

### 為什麼可以這樣混搭

```
主帳號的 GCP 專案
  └─ OAuth client（credentials.json）        ← 應用程式的身分
        ↓ 任何帳號都能授權它
測試帳號的 Google 帳號
  ├─ 授權後產生的 refresh token              ← data/google-token.json
  └─ 日曆「支點課程」                          ← GOOGLE_CALENDAR_ID
```

### 三個必要條件

| # | 條件 | 為什麼 |
|---|---|---|
| 1 | OAuth 同意畫面 **Publishing status = In production** | Testing 狀態只有「Test users」清單裡的帳號能授權，而且 refresh token 7 天失效 |
| 2 | 授權時，瀏覽器要選**測試帳號**（不是主帳號） | token 會綁定當下選的帳號；選錯＝授權給主帳號 |
| 3 | `.env` 的 `GOOGLE_CALENDAR_ID` 要是**測試帳號的日曆 ID** | 用主帳號的日曆 ID 會拿到 403／404 |

> 若 Publishing status 還是 Testing，就到 **Audience → Test users** 把測試帳號加進去，否則授權會直接被拒。

### 操作步驟

**Step 1：確認發布狀態**

Google Cloud Console（主帳號）→ **API 和服務** → **OAuth 同意畫面** → **Audience**
→ 確認 Publishing status 顯示 **In production**

**Step 2：在測試帳號建立日曆**

1. 用**測試帳號**登入 <https://calendar.google.com/>
2. 左側「其他日曆」→ **+** → **建立新日曆** → 名稱 `支點課程`
3. 建立後 → 該日曆右側 ⋮ → **設定和共用**
4. 往下捲到 **日曆 ID**（形如 `xxxxx@group.calendar.google.com`）→ 複製

**Step 3：更新 `.env`**

```dotenv
GOOGLE_CALENDAR_ID=測試帳號的日曆ID
```

> 舊的 `GOOGLE_CALENDAR_ID` 屬於舊帳號，測試帳號的 token 對它沒有權限，一定要換掉。

**Step 4：重新授權（關鍵：選對帳號）**

```bash
./scripts/dev.sh calendar-auth --no-browser
```

- 用 `--no-browser` 才會印出網址讓你控制用哪個瀏覽器／哪個帳號登入
- 把網址貼到**無痕視窗**，或在帳號選擇畫面點 **使用其他帳號** → 選**測試帳號**
- **如果選成主帳號，後面 `calendar-check` 會回 403／404**，那就重跑這一步

**Step 5：驗證**

```bash
# 確認 token 是新鮮的
python3 - <<'EOF'
import json, httpx
d = json.load(open('data/google-token.json'))
r = httpx.post(d['token_uri'], data={
    'client_id': d['client_id'], 'client_secret': d['client_secret'],
    'refresh_token': d['refresh_token'], 'grant_type': 'refresh_token',
}, timeout=20)
print('HTTP', r.status_code, '→', '有效' if r.status_code == 200 else r.text[:200])
EOF

# 確認 token 對測試帳號的日曆有權限
./scripts/dev.sh calendar-check
```

`calendar-check` 顯示「管理 0 筆事件」是正常的（新日曆是空的）。

### 之後要不要把 client 也搬到測試帳號？

**不需要，除非你要長期只用測試帳號。** 現在的分法（主帳號持有 client、測試帳號持有日曆與 token）完全可行，維運上沒有差別。唯一要注意的是：**要重新授權時，你必須能登入主帳號的 GCP Console**（因為 client 在那裡）。若你之後想把測試帳號變成完全獨立、不依賴主帳號，就在測試帳號下另建專案與 client，並重新產生 `credentials.json`。

---

## 7. 授權權限檢查（唯讀，不寫入）

```bash
./scripts/dev.sh calendar-check
```

應顯示類似：

```
Google Calendar 權限確認成功；course-robot 管理 0 筆事件；未修改任何事件
```

**`0 筆`是正常的**（新日曆是空的）。若出現 403／404：

| 錯誤 | 原因 |
|---|---|
| `404 notFound` | Calendar ID 填錯 |
| `403 forbidden` | 這個日曆不屬於這個帳號，或權限不足 |
| 權限確認失敗 | token 不屬於填的那個帳號 → 回到 §4 重做 |

---

## 8. 上 VPS 前的最後檢查

- [ ] §4 授權完成，`data/google-token.json` 已更新
- [ ] §5 的 refresh 實測回 `HTTP 200`
- [ ] Publishing status 是 **In production**（不是 Testing）
- [ ] §6 的 `GOOGLE_CALENDAR_ID` 是新日曆，**不是 `primary`**
- [ ] §7 的 `calendar-check` 通過
- [ ] §3 的三個公開頁面都回 200

之後把 token 傳上 VPS：

```bash
scp data/google-token.json your-user@your-vps:/opt/course-robot/data/google-token.json
```

---

## 9. 之後會遇到的情況

| 情況 | 徵兆 | 處理 |
|---|---|---|
| token 被撤銷（你手動移除授權、改密碼） | 同步時 `RefreshError` | 重跑 §4，把新 token 傳上 VPS |
| 授權範圍變更（未來加功能要新 scope） | 存取被拒 | 重跑 §4（scope 變更必須重新同意） |
| 6 個月未使用 | refresh token 可能失效 | 我們的服務每天執行，不會閒置 |
| 換新帳號 | 舊 token 直接失效 | 重跑本文件全部流程 |

> **測試日曆是必要的**：第一次正式執行前，建議先用一個用完即丟的日曆驗證（建立、修改、刪除各一次），確認事件 ID 與內容都正確，再切到正式的「支點課程」日曆。
