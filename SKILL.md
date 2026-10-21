---
name: askback-student
description: AskBack AI 學生（評量者）。輸入一支補充教學影片（預設 output/askback.mp4）與學生的提問（input/case_input.json），由獨立的 AI 學生做前後測、獨立的評分者依規準評分，算出 0–100 的量化總分（含 95% 信賴區間、四個構念分數、學習成效分、評分者一致性、和上一版的差異）與給 AI 老師的改片建議。當使用者要「評這支 AskBack 影片」「AI 學生打分數」「跑評量迴圈」時使用。
---

# AskBack AI 學生：幫補充教學影片打分數

AI 老師（askback-teacher）針對學生在第 t 秒的提問做了一支補充影片。這個 skill 回答兩個問題：**這支影片有多好？哪裡該改？**

交付物：`eval/runs/<run>/report.md`（分數與解讀）與 `feedback.md`（給老師改片用）。

分數由兩種方法組成（設計理由見 `instrument/README.md`）：

- **規準分（75%）**：多位**獨立**評分者依分析式規準評 4 個構念，比重照作業評分表：內容正確 35、回應提問與適合程度 30、延續原片教法 20、影音品質 15。另有程式量的客觀指標。
- **學習成效分（25%）**：多位**獨立** AI 學生扮演提問的學生，先做前測，再看影片、做後測。答對而且引用得出影片裡講這題的原句才給滿分。

## 你的角色：主持人

你負責跑程式、派工、彙整。**你自己不出題、不作答、不打分數**：你看過影片或腳本，也看過試題答案，你來做會讓分數失真。

以下指令都在「`input/` 所在的那一層」執行。`SKILL` 代表這個 skill 的路徑。

**派工的方法**（下面會用到三次）：把指令檔交給一個**獨立的**執行者，指令只寫「完整照著 `<指令檔的絕對路徑>` 做」。

- 能開 subagent（Claude Code 的 Agent／Task 工具等）：每一份開一個新的 subagent，同一批可以同時跑。
- 不能開 subagent：每一份開一個新的 headless 行程，要給它寫檔權限：
  - `claude -p --permission-mode acceptEdits --add-dir SKILL "完整照著 <路徑> 做"`
  - `codex exec --sandbox workspace-write "完整照著 <路徑> 做"`
- 完成後確認它該寫的檔案存在。沒寫出來就重派一次。
- **不要**在你自己的對話裡扮演他們。

## 0. 準備（一次）

```bash
python3 -m pip install -r SKILL/requirements.txt     # faster-whisper、Pillow、numpy 等
```

需要 ffmpeg（系統沒有就用 imageio-ffmpeg 附的）。有 NVIDIA GPU 時語音辨識用 large-v3，沒有就用 small，第一次會下載模型。

## 1. 出題（只做一次，先於看影片）

`eval/items.lock` 已經存在，就表示這一題出過題了，**跳到第 2 步**。同一題的不同版本影片要用同一份試題，分數才能比較。

```bash
python3 SKILL/scripts/score.py exam        # 產生 eval/exam.md
```

把 `eval/exam.md` **派工**給一個執行者。出題者只能讀題目和原片字幕，不能看影片。它會寫出 `eval/items.json`，並執行 `score.py freeze` 鎖定試題。完成後確認 `eval/items.lock` 存在。
**不要打開 `eval/items.json`**，你知道答案之後，派工時容易不小心洩題。

## 2. 觀察影片

```bash
python3 SKILL/scripts/observe.py                      # 評 output/askback.mp4
python3 SKILL/scripts/observe.py --video 別的.mp4      # 評別支影片（例如同一題的另一個版本）
```

每跑一次會開一個新的 `eval/runs/<影片雜湊>-<第幾次>/`，裡面有客觀指標、逐字稿、截圖拼圖，原片放在旁邊的 `prefix/`。之後的 `panel` 和 `score` 預設用**最新一次** observe 的結果，也可以加 `--run <run id>` 指定。
**要評好幾個版本，就一個版本一個版本跑完 2 → 3 → 4**，不要先把所有版本都 observe 完。

打開 `sheet_segments.jpg` 看一眼，確認截圖和逐字稿正常（不是全黑、有講話）。observe 說語音辨識失敗就先修好環境，不然學生沒辦法引用原句。

## 3. 派出評分小組

```bash
python3 SKILL/scripts/score.py panel                  # 預設 3 位 AI 學生＋3 位評分者
```

它會列出兩批指令檔，而且只列還沒交回結果的：

- **第一批**：每位學生的前測（`student_N_pre.md`）與每位評分者（`rater_N.md`）。全部可以同時派工。
- **第二批**：每位學生的後測（`student_N_post.md`）。**某位學生的前測交回之後**，才派他的後測。前測和後測必須是兩個不同的執行者，這樣前測才不會看到影片。

想加人，就用更大的數字再跑一次 panel（例如 `--raters 5`），它只會列出新增的那幾位。

## 4. 計分

```bash
python3 SKILL/scripts/score.py score --label v1        # label 是這一版的名稱，比較版本時看得懂
```

它會檢查每份結果的格式、套用規準的一致性規則與閘門，再算出總分、四個構念分數、學習成效分、95% 信賴區間與評分者一致性。
如果同一題、同一份試題已經評過別的版本，會多印一行「和上一版比：+x（95% CI …）→ 進步／退步／差異在誤差範圍內」。

- ⚠ 是格式或缺件問題，例如有成員沒交回、JSON 壞掉。照提示重派那一位，再跑一次 score。
- ⚑ 是解讀上要注意的事，例如內容錯誤閘門、角色沒守住、評分者分歧、信賴區間太寬。

## 5. 回報

讀 `report.md`，用幾句話告訴使用者：

1. 總分和 95% 信賴區間，以及規準分、學習成效分。和上一版比較的結果，如果有的話。
2. 四個構念各幾分，最弱的是哪一個、為什麼。〈失分最多的三題〉裡有評分者的證據和時間點。
3. 所有 ⚑ 標記。
4. `feedback.md` 的路徑：要改片的話，把它交給 AI 老師。

## 進化迴圈（AI 老師 ⇄ AI 學生）

```
老師出片 → observe → panel → score → feedback.md → 老師改 lesson.json、重新出片 → observe → panel → score …
```

- 每一輪都**沿用同一份試題**（不要重新出題），每一輪都派**新的**評分者與 AI 學生。
- 給老師的只有 `feedback.md`。**不要把 `report.md`、`eval/items.json` 或學生作答給老師**，否則老師會針對考題改，分數就沒有意義了。
- 判斷有沒有進步，看「和上一版比」的差異信賴區間，不要看兩個總分的區間有沒有重疊。
- 迴圈停下來之後，用新試題做最後確認：`score.py exam`（會自動換成平行複本）→ 派工出題 → 對最後一版跑 2 → 4。分數還站得住，才算數。

## 工具本身的檢驗

累積幾題、幾個版本之後：

```bash
python3 SKILL/scripts/make_probes.py output/askback.mp4 --out probes   # 做已知較差的對照影片（靜音、剩前半、倍速、句間空白）
python3 SKILL/scripts/analyze.py <放各題資料夾的上層> [--groups groups.csv] [--human human.csv]
```

`analyze.py` 會算評分者間信度（ICC、Krippendorff α）、內部一致性（Cronbach α）、重測、試題難度與鑑別度、已知組別差異、規準分與學習成效分的相關，以及和人工評分的相關。怎麼解讀見 `instrument/README.md`。
