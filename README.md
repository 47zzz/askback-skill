# AskBack AI 學生

給 AskBack 補充教學影片打分數的評量 skill。和 AI 老師（[askback-skill](https://github.com/47zzz/askback-skill)）組成迴圈：
老師出片 → 學生評量 → 老師依建議改片 → 再評量。

> 這個 repo 是組內的研究工具，**不是作業的繳交內容**。

## 輸入與輸出

在 AI 老師的同一個工作資料夾（有 `input/` 與 `output/` 的那一層）執行：

| | 檔案 |
|---|---|
| 輸入 | `output/askback.mp4`（或任何 `--video`）、`input/case_input.json`（學生的提問），有的話加上 `input/prefix.mp4`、`input/prefix.vtt` |
| 輸出 | `eval/runs/<run>/report.md`：總分 0–100、95% 信賴區間、四個構念分數、學習成效分、評分者一致性 |
| | `eval/runs/<run>/feedback.md`：給 AI 老師的改片建議（刻意不含試題） |
| | `eval/history.jsonl`：每一版的分數紀錄 |

## 流程

```mermaid
flowchart TD
    CASE["input/case_input.json<br/>學生的提問・迷思・已知概念"]
    EXAM["① 出題者（沒看過影片）<br/>學習目標＋6–12 題<br/>target・diagnostic・near・far・control"]
    LOCK["score.py freeze<br/>檢查藍圖、鎖定試題"]
    OBS["② observe.py<br/>客觀指標・逐字稿・截圖拼圖・原片對照"]
    PANEL["③ score.py panel<br/>每位成員一份指令檔"]
    STU["AI 學生 ×3（各自獨立）<br/>扮演提問的學生：前測 → 看影片 → 後測<br/>改答要引用影片原句"]
    RAT["評分者 ×3（各自獨立）<br/>先列學科陳述與子問題，再依規準 1–5 分"]
    SCORE["④ score.py score<br/>一致性規則・閘門・r_wg・信賴區間"]
    OUT["report.md・feedback.md・history.jsonl"]
    TEACH["AI 老師改 lesson.json、重新出片"]

    CASE --> EXAM --> LOCK --> OBS --> PANEL
    PANEL --> STU --> SCORE
    PANEL --> RAT --> SCORE
    SCORE --> OUT
    OUT -->|feedback.md（不含試題）| TEACH -->|同一份試題| OBS
```

## 分數怎麼來

```
總分 ＝ 0.75 × 規準分 ＋ 0.25 × 學習成效分　（再套用閘門）
```

- **規準分**：照作業評分表的比重。內容正確 35、回應提問與適合程度 30、延續原片教法 20、影音品質 15。每個構念 4–5 題，都有行為定錨的 1–5 分量表，再加上程式量的客觀指標（長度、響度、說話時間比例、字錯率、與原片的語速比）。
- **學習成效分**：AI 學生扮演提問的那位學生，先做前測、看影片、再做後測。答對但沒引用影片原句只給一半分；另有控制題，用來偵測 AI 學生是不是「假裝不會」。
- **閘門**：沒畫面或沒聲音記 0 分；過半評分者發現內容錯誤，總分上限 60；長度不在 30–90 秒，上限 70；沒有 AI 標示，影音品質上限 40。

設計依據（構念定義、效度論證、信度指標、防止針對考題改片）見 [`instrument/README.md`](instrument/README.md)。

## 內容

```
SKILL.md                 agent 讀這份：主持人怎麼跑完一次評量
instrument/README.md     評量設計：構念、效度與信度、進化迴圈、參考文獻
instrument/rubric.json   規準：4 個構念、17 題、1–5 分定錨描述、一致性規則；AI 學生自評 4 題
instrument/scoring.json  權重、客觀指標換算、閘門、信度門檻（調整計分只改這裡）
instrument/test_spec.md  出題規格：學習目標、題目藍圖、題型
prompts/                 AI 學生與評分者的指令範本（score.py panel 會填好）
scripts/observe.py       觀察：客觀指標、逐字稿、截圖拼圖、原片對照
scripts/score.py         freeze（鎖定試題）、panel（派工）、score（計分與報告）
scripts/analyze.py       跨影片的信度與效度分析（ICC、α、重測、試題分析、已知組別、效標關聯）
scripts/make_probes.py   做已知較差的對照影片（靜音、剩前半、倍速、句間空白）
scripts/psychometrics.py 統計函式（只用標準函式庫）
fonts/                   Noto Sans TC（拼圖標字用，SIL OFL 1.1）
```

## 安裝

```bash
python3 -m pip install -r requirements.txt
ln -s "$(pwd)" ~/.claude/skills/askback-student     # 在 Claude Code 裡用 /askback-student
```
