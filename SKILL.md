---
name: askback-student
description: AskBack AI 學生（評量者）。輸入一支補充教學影片（預設 output/askback.mp4）與學生的提問（input/case_input.json），由獨立的 AI 學生做前後測、獨立的評分者依規準評分，算出 0–100 的量化總分（含 95% 信賴區間、四個構念分數、學習成效分、評分者一致性）與給 AI 老師的改片建議。當使用者要「評這支 AskBack 影片」「AI 學生打分數」「跑評量迴圈」時使用。
---

# AskBack AI 學生：幫補充教學影片打分數

AI 老師（askback-teacher）針對學生在第 t 秒的提問做了一支補充影片。這個 skill 回答：**這支影片有多好？哪裡該改？**

交付物：`eval/runs/<run>/report.md`（分數與解讀）與 `feedback.md`（給老師改片用）。總分印在最後一行。

分數由兩種方法組成（設計理由見 `instrument/README.md`）：

- **規準分（75%）**：多位**獨立**評分者依分析式規準評 4 個構念，比重照作業評分表：內容正確 35、回應提問與適合程度 30、延續原片教法 20、影音品質 15。另有程式量的客觀指標。
- **學習成效分（25%）**：多位**獨立** AI 學生扮演提問的學生，先做前測、看影片、再做後測，看迷思有沒有被修正、會不會遷移到新例子。

## 你的角色：主持人，不是評分者

你負責出題（或請人出題）、跑程式、派工、彙整。**你自己不打分數，也不作答**：你看過試題答案，你作答或評分都會讓分數失真。

以下指令都在「`input/` 所在的那一層」執行。`SKILL` 代表這個 skill 的路徑。

## 0. 準備（一次）

```bash
python3 -m pip install -r SKILL/requirements.txt     # faster-whisper、Pillow、numpy 等
```

需要 ffmpeg（系統沒有會用 imageio-ffmpeg 附的）。有 NVIDIA GPU 時語音辨識自動用 large-v3，沒有就用 small。

## 1. 出題（先於看影片）

`eval/items.lock` 已經存在，就表示這一題已經出過題，**直接跳到第 2 步**。同一題的不同版本影片要用同一份試題，分數才能比較。

沒有的話：

- **出題者只能讀 `input/case_input.json`**（和 `input/prefix.vtt`，如果有），**不能看要評的影片，也不能看老師的腳本**。
- 如果這支影片是你自己（或這個對話）做的，就**交給一個 subagent 出題**，指令是：「只讀 `input/case_input.json` 與 `SKILL/instrument/test_spec.md`，照規格寫出 `eval/items.json`，不要開其他檔案」。
- 規格見 `instrument/test_spec.md`：2–4 個學習目標；6–12 題，含 target ≥ 2、diagnostic ≥ 2、near ≥ 1、far ≥ 1、control ≥ 1；只用選擇、是非、數字、簡答。

然後鎖定：

```bash
python3 SKILL/scripts/score.py freeze
```

不符合藍圖它會說哪裡不對，改到通過為止。

## 2. 觀察影片

```bash
python3 SKILL/scripts/observe.py                      # 評 output/askback.mp4
python3 SKILL/scripts/observe.py --video 別的.mp4      # 評別支影片（例如同一題的另一個版本）
```

它會量出客觀指標（長度、響度、說話時間比例、語速、與老師腳本的字錯率），抽出逐字稿和截圖拼圖，放在 `eval/runs/<run>/observe/`。
**打開 `sheet_segments.jpg` 看一眼**，確認截圖和逐字稿正常（例如不是全黑、不是沒有聲音）。有問題就先處理，不然後面的評分都白做。

## 3. 派出評分小組

```bash
python3 SKILL/scripts/score.py panel                  # 預設 3 位 AI 學生＋3 位評分者
python3 SKILL/scripts/score.py panel --students 2 --raters 2   # 快速版（信賴區間會比較寬）
```

它會產生 `eval/runs/<run>/panel/student_*.md` 與 `rater_*.md`，每份都是一位成員的完整指令。

**每一份都交給一個獨立的執行者**，彼此看不到對方：

- 能開 subagent（Claude Code 的 Agent／Task 工具等）：每一份開一個 subagent，指令只寫「完整照著 `<那份 .md 的路徑>` 做」。可以全部同時跑。
- 不能開 subagent：每一份開一個新的 headless 行程，例如 `claude -p "完整照著 <路徑> 做"` 或 `codex exec "完整照著 <路徑> 做"`。
- **不要**在你自己的對話裡依序扮演他們，因為你已經看過試題答案。

每位成員會把結果寫到 `eval/runs/<run>/panel/out/`。全部完成後，確認每一份 JSON 都在。

## 4. 計分

```bash
python3 SKILL/scripts/score.py score --label v1        # label 是這一版的名稱，之後比較版本用
```

它會檢查每份結果的格式、套用規準的一致性規則與閘門，然後算出總分、四個構念、學習成效分、95% 信賴區間與評分者一致性 r_wg。
印出的 ⚠ 是格式或一致性問題，⚑ 是需要注意的解讀。r_wg < 0.7 的構念代表評分者意見分歧：要的話可以再派 2 位評分者（`panel --raters 5` 會補產生 4、5 號），然後重新計分。

## 5. 回報

讀 `report.md`，用幾句話告訴使用者：

1. 總分與 95% 信賴區間，以及規準分、學習成效分。
2. 四個構念各幾分，最弱的是哪一個、為什麼（引用評分者的證據與時間點）。
3. 所有 ⚑ 標記，例如內容錯誤閘門、角色沒守住、評分者分歧。
4. `feedback.md` 的路徑：要改片的話，把它交給 AI 老師。

## 進化迴圈（AI 老師 ⇄ AI 學生）

```
老師出片 → observe → panel → score → feedback.md → 老師改 lesson.json、重新 render → observe → panel → score …
```

- 每一輪都**沿用同一份試題**（不要重新 freeze），每一輪都派**新的**評分者與 AI 學生。
- `eval/history.jsonl` 記錄每一版的分數。兩版差距小於信賴區間時，不算真的進步。
- 給老師的只有 `feedback.md`。**不要把試題或 report.md 的試題細節給老師**，否則老師會針對考題改，分數就失去意義。
- 迴圈停下來之後，換一份新試題做最後確認（`score.py freeze --new-form`，由沒看過影片的人出題），確定分數還站得住。

## 工具本身的檢驗

評量工具也要被評量。累積幾題、幾個版本之後：

```bash
python3 SKILL/scripts/make_probes.py output/askback.mp4 --out probes   # 做已知較差的對照影片
python3 SKILL/scripts/analyze.py <放各題資料夾的上層> [--groups groups.csv] [--human human.csv]
```

`analyze.py` 會算評分者間信度（ICC、Krippendorff α）、內部一致性（Cronbach α）、重測、試題難度與鑑別度、已知組別差異、規準分與學習成效分的相關，以及和人工評分的相關。怎麼解讀見 `instrument/README.md`。
