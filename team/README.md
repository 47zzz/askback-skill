# 組員協作區

這個資料夾是我們自己用的：討論、文件、測試成果。**它不屬於 skill**，助教的 agent 不會用到，也不應該用到。

```
team/
├── README.md         這份：怎麼一起改、怎麼測、怎麼存成果
├── docs/             設計文件（流程說明等）
├── outputs/          各題的測試成果與進度表
└── save_output.sh    把剛跑完的一題存進 outputs/
```

## repo 的兩個身分

| 位置 | 給誰 | 規則 |
|---|---|---|
| 根目錄（`README.md`、`SKILL.md`、`scripts/`、`examples/`、`fonts/`） | 助教的 agent | 這就是繳交的作品。改這裡會直接影響分數，改完一定要重測 |
| `team/` | 我們自己 | 隨意放，但不要放金鑰、不要放原片影片檔 |

繳交時交的就是這個 repo 的連結，所以 **`main` 分支隨時都要是能跑的狀態**。

## 第一次使用

```bash
git clone <repo 連結> askback-skill
cd askback-skill
python3 -m pip install -r requirements.txt
python3 -m pip install faster-whisper      # 選用：沒有字幕的題目要語音辨識
```

想在自己的 Claude Code 裡用 `/askback-teacher` 呼叫：

```bash
ln -s "$(pwd)" ~/.claude/skills/askback-teacher
```

## 測一題

題目要自己從作業素材包（`askback-junyi/`）切，素材不放進 repo：

```bash
cd <askback-junyi 素材包>
python3 prepare_case.py V15 --out ~/askback-test      # 產生 ~/askback-test/V15/
cd ~/askback-test/V15
mkdir input && mv case_input.json prefix.* input/
```

然後在 `~/askback-test/V15` 這一層開 Claude Code 或 Codex，用**助教的那句話**測（才是真實情況）：

```
請下載這個 repo：<repo 連結>，完整讀過它的說明後，照它的做法為 input/ 裡的這一題產生補充教學影片。
```

不要多給提示。跑完會有 `output/askback.mp4` 和 `output/preview.jpg`。

## 存成果

在同一層執行：

```bash
bash <repo 路徑>/team/save_output.sh
```

它會把影片、預覽圖、腳本複製到 `team/outputs/<題號>/`，並印出接下來的 git 指令。
存完請更新 `team/outputs/README.md` 的進度表。

## 看成果時檢查什麼

對照評分標準：

- **內容正確（35%）**：有沒有任何一句科學或數學上不精確？算式都對嗎？
- **回應提問、適合程度（30%）**：學生的困惑真的被解開了嗎？有沒有超出年段的用詞？
- **延續原片教法（20%）**：形式、例子、語氣、節奏和原片接得上嗎？
- **影音品質（15%）**：聽得清楚、字沒有疊在一起、長度在 30–90 秒內？

發現問題時，先想「是這一題的腳本寫壞了，還是 `SKILL.md` 沒講清楚」。後者才需要改 skill。

## 一起改的規則

- 小修（錯字、文件）可以直接推 `main`。
- 改 `SKILL.md` 或 `scripts/`：開分支 → 至少重測一題確定還能出片 → 發 PR 或跟組員說一聲 → 合併。
- commit 訊息寫做了什麼，例如 `skill: 加上銜接句的限制`、`render: 修分數的行高`、`output: V15`。
- 不要 commit：金鑰、`input/`、`work/`、`output/`（已在 `.gitignore`）、原片影片。

## 目前還沒做的事

- [ ] 讓全新的 agent 只讀 repo 說明跑完一題（Claude Code 與 Codex 各一次）
- [ ] `slides` 形式還沒有完整題目測過
- [ ] 在 Linux 上測一次
- [ ] `scripts/paint.py`（生圖）與 OpenAI 的語音、辨識備援沒有金鑰可測
- [ ] 決定 `examples/` 與 `team/outputs/` 裡的三題腳本要不要換成非題庫的例子（它們是正式 20 題之一）
- [ ] 20 題都跑過一輪，填滿進度表
