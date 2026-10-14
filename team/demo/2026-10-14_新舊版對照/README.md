# 2026-10-14 新舊版對照（組內交流用，不是繳交內容）

打開 `index.html`。三題（V01、V03、V13）用**同一份 lesson.json**，比較三種流程，另附原片：

| 欄位 | 流程 | 檔案 |
|---|---|---|
| 舊版 | edge-tts 固定聲音＋Pillow 逐格畫（Mac） | 直接引用 `team/outputs/Vxx/askback.mp4` |
| 中間 | edge-tts＋Remotion（只換畫面） | `Vxx/mid.mp4` |
| 新版 | BreezyVoice 在 RTX 3090 上 clone 原片老師＋Remotion | `Vxx/new.mp4` |
| 原片 | 均一原片，可跳到學生提問點 | 不進 repo（`Vxx/orig.mp4` 已列入 .gitignore），頁面上有 YouTube 連結 |

每題資料夾另有：`ref.wav`（從原片自動挑出的 clone 參考音檔）、`profile.json`（老師的音高、語速、參考音檔資訊）、`prev_new.jpg`（新版每句講完的畫面）。

`製作紀錄/` 是在戰艦（3090 計算節點＋登入節點）上產生這些檔案的腳本，路徑寫死在戰艦上，只當紀錄：
`run_gpu.sh`（計算節點：look.py＋clone 配音）→ `run_login.sh`（登入節點：用語音快取跑 Remotion，因為計算節點的 Chrome 缺 libnss3）→ `compare.py`（產生對照頁；原片區塊與 YouTube 連結是之後手動加的）。

新版含原片老師的聲線（AI clone），只在組內看，不要另外轉傳。
