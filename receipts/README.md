# Receipts behind the Hallbook page

Raw rows and the scripts that produce the numbers on the Hallbook page.

- `stage5.jsonl`: does the agent use Hallbook? 24 coding tasks, conditions `ask` ("use the Aletheore skills"), `snippet` (one line in AGENTS.md) and `none`.
- `stage5_text.jsonl`: the same question on 24 text tasks (ask vs none).
- `stage6.jsonl`: does Hallbook help? 40 hidden-test fixtures, conditions baseline, hallbook, hint, inject, oracle, ask.
- `ANALYSIS_stage6.md`: analysis of an earlier, partial snapshot of stage 6 (456 runs).
- `quick_paired.py`: paired bootstrap on DeepSeek V4 Flash over fixtures f11 to f40 (run from this folder: `python3 quick_paired.py`). Its output is the source of the page's pass-rate and token figures.
- `judge_stage5_summary.json`: blind judging of ask vs none answers on the text tasks.
