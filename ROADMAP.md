# Tuneup 功能藍圖

狀態：✅ 已完成　🟡 進行中　⬜ 未開始

「已完成」代表程式已寫好並通過自動測試或隔離環境的流程測試；尚未在真實系統上逐項手動驗證的部分，列在文末「待實機驗證」。

設計規則見 [DESIGN.md](DESIGN.md)。所有會改動系統的功能都要走「預覽 → 快照 → 套用 → 可還原」。

---

## 第 1 階段：調校（涵蓋 GNOME Tweaks 全部功能）

### 寫入機制
- ✅ 套用變更：把未套用的項目寫入 gsettings
- ✅ 單項還原為預設值，並標示哪些項目被改過
- ✅ 捨棄未套用的變更
- ✅ 系統上沒有的 schema／key 自動隱藏（已完成讀取端）

### GNOME Tweaks 功能對照
✅ 下表各項都已實作，除了另外標示 ⬜ 的。右欄是對應的 gsettings；標「＋」的是 Tweaks 沒有、Tuneup 額外提供的。

**外觀**
| 項目 | 來源 |
|---|---|
| 色彩模式 ＋ | `org.gnome.desktop.interface color-scheme` |
| 強調色 ＋ | `… interface accent-color` |
| 游標主題 | `… interface cursor-theme` |
| 游標大小 ＋ | `… interface cursor-size` |
| 圖示主題 | `… interface icon-theme` |
| 舊版應用程式主題（GTK 主題） | `… interface gtk-theme` |
| Shell 主題 | `org.gnome.shell.extensions.user-theme name`（需 User Themes 擴充功能；沒安裝時這一列不顯示） |
| 桌布（淺色／深色） | `org.gnome.desktop.background picture-uri`、`picture-uri-dark` |
| 桌布顯示方式 | `… background picture-options` |
| 動畫效果 | `… interface enable-animations` |

**字型**
| 項目 | 來源 |
|---|---|
| 介面字型 | `… interface font-name` |
| 文件字型 | `… interface document-font-name` |
| 等寬字型 | `… interface monospace-font-name` |
| 舊版視窗標題字型 | `org.gnome.desktop.wm.preferences titlebar-font` |
| 微調（Hinting） | `… interface font-hinting` |
| 反鋸齒 | `… interface font-antialiasing` |
| 次像素順序 ＋ | `… interface font-rgba-order` |
| 縮放係數 | `… interface text-scaling-factor` |

**視窗**
| 項目 | 來源 |
|---|---|
| 標題列按鈕位置（左／右） | `… wm.preferences button-layout` |
| 顯示最大化按鈕 | 同上 |
| 顯示最小化按鈕 | 同上 |
| 雙擊標題列 | `… wm.preferences action-double-click-titlebar` |
| 中鍵點擊標題列 | `… action-middle-click-titlebar` |
| 右鍵點擊標題列 | `… action-right-click-titlebar` |
| 對話框附著於父視窗 | `org.gnome.mutter attach-modal-dialogs` |
| 邊緣貼齊 | `org.gnome.mutter edge-tiling` |
| 新視窗置中 | `org.gnome.mutter center-new-windows` |
| 以右鍵調整視窗大小 | `… wm.preferences resize-with-right-button` |
| 視窗動作按鍵（Alt／Super／停用） | `… wm.preferences mouse-button-modifier` |
| 視窗焦點（點擊／滑過／跟隨滑鼠） | `… wm.preferences focus-mode` |
| 取得焦點時提升視窗 | `… wm.preferences auto-raise` |

**工作區**
| 項目 | 來源 |
|---|---|
| 動態／固定工作區 | `org.gnome.mutter dynamic-workspaces` |
| 工作區數量 | `… wm.preferences num-workspaces` |
| 僅在主螢幕使用工作區 | `org.gnome.mutter workspaces-only-on-primary` |

**頂部列與 Dock**
| 項目 | 來源 |
|---|---|
| 時鐘：星期、日期、秒數 | `… interface clock-show-weekday`、`clock-show-date`、`clock-show-seconds` |
| 時鐘格式（12／24 小時）＋ | `… interface clock-format` |
| 行事曆顯示週數 | `org.gnome.desktop.calendar show-weekdate` |
| 電量百分比 | `… interface show-battery-percentage` |
| 活動總覽熱角 | `… interface enable-hot-corners` |
| Dock：位置、圖示大小、永遠顯示、延伸到邊緣、自動隱藏、垃圾桶、掛載裝置、點擊動作 ＋ | `org.gnome.shell.extensions.dash-to-dock …` |
| 桌面圖示：家目錄、垃圾桶、大小、起始角落 ＋ | `org.gnome.shell.extensions.ding …` |

**鍵盤與滑鼠**
| 項目 | 來源 |
|---|---|
| 顯示所有輸入來源 | `org.gnome.desktop.input-sources show-all-sources` |
| Emacs 輸入模式 | `… interface gtk-key-theme` |
| 總覽快捷鍵（左 Super／右 Super） | `org.gnome.mutter overlay-key` |
| 其他配置選項 | `… input-sources xkb-options`：獨立的「鍵盤配置選項」頁，列出系統提供的全部 22 類、217 個選項（說明為英文，系統沒有繁中翻譯） |
| 按鍵重複延遲與速度 ＋ | `org.gnome.desktop.peripherals.keyboard delay`、`repeat-interval` |
| 中鍵貼上 | `… interface gtk-enable-primary-paste` |
| 指標位置（按 Ctrl 顯示） | `… interface locate-pointer` |
| 滑鼠：加速設定檔、速度、自然捲動、左手模式 | `org.gnome.desktop.peripherals.mouse …` |
| 觸控板：打字時停用 | `… peripherals.touchpad disable-while-typing` |
| 觸控板：點擊模擬（手指／區域／停用） | `… touchpad click-method` |
| 觸控板：加速設定檔、速度、自然捲動、輕觸點擊 | `… touchpad …` |

**音效**
| 項目 | 來源 |
|---|---|
| 系統音效主題 | `org.gnome.desktop.sound theme-name` |
| 事件音效 | `… sound event-sounds` |
| 音量可超過 100% | `… sound allow-volume-above-100-percent` |

**音訊裝置**（對應 macOS 的「音訊 MIDI 設定」，Tweaks 沒有）＋
- ✅ 預設輸出、預設輸入
- ✅ 全系統取樣率與緩衝區大小（立即生效，登出後還原）
- ✅ 自動切換取樣率
- ✅ 設為開機預設：寫入 `~/.config/pipewire/pipewire.conf.d/90-tuneup.conf` 並重新啟動音效服務
- ✅ 每個裝置的模式（立體聲、環繞、專業音訊、關閉）與目前的格式
- ✅ MIDI 裝置清單
- ✅ 以上變更都記入歷史，可以還原
- ⬜ 聚集裝置、多重輸出裝置
- ⬜ 喇叭配置（聲道對應）
- ⬜ 每個裝置各自的取樣率（PipeWire 是全系統共用，需要改用裝置層級的設定檔）

**電源**
| 項目 | 來源 |
|---|---|
| 螢幕關閉時間 ＋ | `org.gnome.desktop.session idle-delay` |
| 閒置時（接電源／用電池）＋ | `org.gnome.settings-daemon.plugins.power sleep-inactive-*-type` |
| 電源鍵 ＋ | `… power power-button-action` |
| 閒置時調暗螢幕 ＋ | `… power idle-dim` |
| 闔上螢幕時不暫停 | 與 Tweaks 相同：登入時啟動一個持有 handle-lid-switch 抑制鎖的程序 |

**隱私** ＋
最近檔案、診斷回報、定位、自動清除暫存檔與垃圾桶、螢幕鎖定、通知橫幅。

**啟動應用程式**
- ✅ 列出 `~/.config/autostart` 的項目
- ✅ 從已安裝的應用程式新增
- ✅ 移除

---

## 第 2 階段：設定快照與歷史

- ✅ 每次套用前自動記錄舊值與新值
- ✅ 時間軸：依時間列出每一次變更，可展開看差異
- ✅ 僅還原某一次變更
- ✅ 還原到某個時間點
- ✅ 還原本身也記錄，可以再退回
- ✅ 手動建立完整設定快照：由「設定檔」提供（建立設定檔即記下全部調校值與擴充功能狀態）

## 第 3 階段：擴充功能操作

- ✅ 啟用／停用
- ✅ 全部擴充功能總開關（安全模式）
- ✅ 從 extensions.gnome.org 安裝
- ✅ 解除安裝
- ✅ 檢查更新
- ✅ 搜尋線上擴充功能
- ✅ 開啟擴充功能的偏好設定
- ✅ 擴充功能造成當機時的安全啟動：`python3 tuneup.py --safe-mode` 不開視窗就暫停所有擴充功能，可從文字終端機執行

### 擴充功能市集與管理
- ✅ 市集：搜尋、四種排序（熱門、最新、下載次數、名稱）、載入更多
- ✅ 市集詳細頁：截圖、完整說明、作者、下載次數、連到網頁、安裝
- ✅ 從自己下載的 .zip 檔安裝
- ✅ 啟用／停用已在這台機器實測（停用後從歷史還原）

### 主題與圖示（新頁面）
- ✅ 四種主題：圖示、游標、應用程式（GTK）、Shell
- ✅ 管理：列出已安裝的主題、套用（記入歷史）、移除使用者安裝的主題
- ✅ 市集：gnome-look.org 的搜尋、排序、縮圖、詳細頁、選擇檔案安裝
- ✅ 從自己下載的壓縮檔安裝
- ✅ 下載防護：只允許 https、限制大小、核對 MD5、拒絕會寫到主題資料夾以外的壓縮檔
- ⬜ 桌布、字型、音效主題的市集
- ⬜ 主題更新檢查

## 第 4 階段：設定檔

- ✅ 從目前狀態建立
- ✅ 套用（走快照流程）
- ✅ 套用前預覽差異
- ✅ 更新、刪除
- ✅ 重新命名
- ✅ 匯出為 `.tuneup` 檔、從檔案匯入
- ✅ 從網址匯入

## 第 5 階段：全域搜尋

- ✅ 搜尋個別設定項：篩出含有該設定的頁面，點進去後焦點會移到符合的那一列
- ⬜ 搜尋擴充功能與動作

---

## 第 6 階段：備份與還原

四個層級，由輕到重。第 1 層是第 2 階段的設定快照。

**第 2 層：系統還原點**（對應 Windows 系統還原點）
- 🟡 包裝 Timeshift：一鍵安裝、建立還原點、列出與刪除還原點；還原與排程仍是開啟 Timeshift 自己的視窗
- ⬜ 排程
- ⬜ 套件升級與更換驅動前自動建立
- 這台機器的系統碟是 ext4，只能用 rsync 模式，建立需要數分鐘。Timeshift 已於 2026-11-12 透過 Tuneup 的安裝按鈕裝好

**第 3 層：個人檔案備份**（對應 Windows 檔案歷程記錄）
- 🟡 包裝 Déjà Dup：未安裝時可一鍵安裝；已安裝時可立即備份。其餘設定是開啟 Déjà Dup 自己的視窗
- Déjà Dup 已於 2026-11-12 透過 Tuneup 的安裝按鈕裝好

**第 4 層：完整映像檔**（對應 Windows 系統映像備份）
- 🟡 顯示系統碟用量與需要的空間；尚未檢查外接硬碟的可用空間
- 🟡 引導製作 Clonezilla 開機隨身碟：目前只有連到官方說明
- ⬜ 驗證映像
- 系統執行中無法做出可靠的完整映像，所以這一層是「引導與檢查」，不是一鍵完成

**共用**
- 🟡 備份健康狀態顯示在總覽：上次還原點與個人檔案備份的時間、超過 30 天沒備份的提醒；空間不足尚未檢查

## 第 7 階段：其他模組

**應用程式**
- ✅ 列出 Snap 應用程式與手動安裝的 apt 套件，顯示大小（Flatpak 這台沒有安裝，尚未支援）
- ✅ 搜尋 apt 與 Snap 並安裝、移除（apt 會先模擬並列出連帶移除的套件，碰到系統必要套件就拒絕）、更新
- 🟡 軟體來源：列出所有 apt 來源與啟用狀態，並可開啟系統的「軟體與更新」；尚未在 Tuneup 內新增、停用或移除

**系統更新**
- ✅ 列出可用更新、重新檢查、全部更新、更新 Snap、更新紀錄、暫緩特定套件

**系統清理**
- ✅ 縮圖快取、垃圾桶、套件快取、系統日誌、不再需要的套件（含舊核心）；先顯示大小，確認後才清

**驅動與硬體**
- 🟡 顯示 ubuntu-drivers 建議的驅動，並可開啟系統的「額外驅動程式」；尚未在 Tuneup 內直接切換
- 🟡 列出裝置韌體版本與可更新數量；安裝更新仍需在終端機執行 fwupdmgr update
- ✅ 電池健康（容量、設計容量、充電次數）

**問題排查**
- ✅ 查看本次開機的系統錯誤記錄（相同訊息合併）；擴充功能的錯誤顯示在擴充功能頁

## 第 8 階段：基礎建設

- ✅ 權限：系統層級操作統一透過 pkexec 取得授權，每次都先跳確認視窗
- ⬜ 模組 API：每個功能註冊自己的頁面、動作與還原邏輯
- ⬜ 外掛 SDK
- 🟡 打包：關於視窗、「加入應用程式選單」、deb 套件（版本號含建置序號，重新建置後可直接升級）與 CI；Flatpak、專屬圖示、自動發佈尚未製作
- ✅ 擴充功能的實機測試：`tests/shell/check_dock_effects.py` 在隔離的 GNOME Shell 裡檢查放大、彈跳與懸停預覽
- ⬜ 翻譯改用 gettext，方便交給翻譯平台

---

## 已完成

- ✅ GNOME 設定風格的介面與設計原則
- ✅ 多語言：繁體中文、英文，即時切換
- ✅ 讀取真實資料：系統資訊、已安裝的擴充功能、gsettings 目前值、線上熱門擴充功能

## 待實機驗證

以下功能已實作，但只在隔離環境（記憶體後端、暫存資料夾）測過，或完全沒有實際點過：

- 擴充功能的啟用／停用、總開關、安裝、解除安裝、檢查更新（會改動真實的 GNOME Shell，尚未實測）
- 字型選擇對話框、桌布選擇、設定檔匯出與從檔案匯入的檔案對話框
- 從網址匯入設定檔
- 啟動應用程式的移除（新增已在暫存資料夾測過）
- 系統清理的五個清理動作、安裝 Timeshift／Déjà Dup、建立還原點（讀取與顯示已在這台機器確認，實際執行需要你輸入密碼，沒有測過）
- 音訊裝置：讀取已在這台機器確認，寫入只實測過「強制緩衝區大小再還原」；切換預設裝置、切換裝置模式、強制取樣率、開機預設（會重新啟動音效服務）都沒有實測
- 應用程式的移除、系統更新的三個按鈕、設定檔重新命名的對話框（清單讀取已在這台機器確認；移除與更新需要你輸入密碼，沒有測過）
- 搜尋後安裝應用程式、暫緩與取消暫緩套件、關於視窗、加入應用程式選單（搜尋、更新紀錄、安全模式指令已測過；其餘沒有實際按過）
- 鍵盤配置選項的 217 個開關（共用調校的寫入流程，但沒有逐一確認效果）、闔上螢幕時不暫停的實際效果（抑制鎖的建立與移除已測，這台是桌機，無法測闔上螢幕）
- 主題：從市集下載安裝、從檔案安裝、套用、移除（市集瀏覽與詳細頁已對真實網站確認；解壓縮防護有自動測試；實際下載安裝沒有測，因為那等於替你下載第三方檔案）
- 擴充功能：從市集安裝（需要你在 GNOME Shell 的確認視窗按同意）、從檔案安裝、解除安裝
- 電池資訊（這台是桌機，沒有電池可測；只測過解析函式）
- 各調校項寫入後，系統是否真的照著改變（寫入 gsettings 已測，實際效果未逐項確認）
