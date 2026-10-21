# Evals — Genkit Python

## Two types of evaluators

1. **Built-in** — ship with `genkit-evaluators`, register with
   `register_genkit_evaluators(ai)`. Regex / deep_equal do not need
   `GoogleAI()` or `GEMINI_API_KEY`.
2. **BYO** — `ai.define_evaluator()` with your own scoring logic (including
   LLM judges).

## Install

```bash
uv add genkit-evaluators
```

## Dataset format

```json
[
  {"testCaseId": "case1", "input": "x", "output": "banana", "reference": "ba?a?a"},
  {"testCaseId": "case2", "input": "x", "output": "apple",  "reference": "ba?a?a"}
]
```

Fields: `testCaseId`, `input`, `output`, `reference` (optional for some
evaluators).

## Built-in evaluators

```python
from genkit_evaluators import register_genkit_evaluators
register_genkit_evaluators(ai)
```

Registered: `genkitEval/regex`, `genkitEval/deep_equal`, `genkitEval/jsonata`.
`eval:run` needs a Genkit runtime:

```bash
# terminal A
genkit start -- uv run src/main.py
# terminal B
genkit eval:run datasets/my_dataset.json --evaluators=genkitEval/regex
```

One-shot (starts the app as the runtime):

```bash
genkit eval:run datasets/my_dataset.json --evaluators=genkitEval/regex -- uv run src/main.py
```

`No runtimes found` means nothing is registered — start the app or use `--`.

Programmatic — one evaluator per call (`evaluator=`, singular):

```python
result = await ai.evaluate(
    dataset=my_dataset,
    evaluator='byo/my_eval',  # not evaluators=[...]
)
# EvalResponse — use result.root (not .results)
```

Add `--output=eval_out.json` on CLI for machine-readable pass/fail.

## BYO evaluator

```python
from genkit.evaluator import BaseDataPoint, Details, EvalFnResponse, EvalStatusEnum, Score

async def my_eval(datapoint: BaseDataPoint, _options: dict | None = None) -> EvalFnResponse:
    output = str(datapoint.output or '')
    reference = str(datapoint.reference or '')
    passed = output.strip() == reference.strip()
    return EvalFnResponse(
        test_case_id=datapoint.test_case_id or '',
        evaluation=Score(
            score=1.0 if passed else 0.0,
            status=EvalStatusEnum.PASS if passed else EvalStatusEnum.FAIL,
            details=Details(reasoning='Exact match check'),
        ),
    )

ai.define_evaluator(
    name='byo/my_eval',
    display_name='My Eval',
    definition='Checks exact match of output vs reference.',
    fn=my_eval,
)
```

## LLM-based judge

Keep a judge prompt next to the app and parse a score from the model.

`prompts/judge.prompt`:

```
---
model: googleai/gemini-flash-latest
input:
  schema:
    output: string
    reference: string
---
Score how well the candidate matches the reference from 0.0 to 1.0.
Reply with ONLY a number (e.g. 0.8), nothing else.

Reference: {{reference}}
Candidate: {{output}}
```

```python
import re

async def llm_eval(datapoint: BaseDataPoint, _options: dict | None = None) -> EvalFnResponse:
    rendered = await ai.prompt('judge').render(
        input={'output': str(datapoint.output), 'reference': str(datapoint.reference)}
    )
    response = await ai.generate(
        model='googleai/gemini-flash-latest',
        messages=rendered.messages,
    )
    m = re.search(r'0?\.\d+|1\.0|[01]', (response.text or '').strip())
    score = float(m.group(0)) if m else 0.0
    return EvalFnResponse(
        test_case_id=datapoint.test_case_id or '',
        evaluation=Score(
            score=score,
            status=EvalStatusEnum.PASS if score >= 0.5 else EvalStatusEnum.FAIL,
        ),
    )

ai.define_evaluator(
    name='byo/llm_judge',
    display_name='LLM Judge',
    definition='Scores candidate vs reference with a judge model.',
    fn=llm_eval,
)
```

Built-in `genkitEval/regex` scores are `True`/`False`; BYO scores are usually
`0.0`/`1.0` — normalize before aggregating.

## CLI

```bash
genkit eval:run datasets/my_dataset.json --evaluators=byo/my_eval
genkit eval:run datasets/my_dataset.json --evaluators=genkitEval/regex,byo/my_eval
```

Results appear in the Dev UI under **Evaluate**.
