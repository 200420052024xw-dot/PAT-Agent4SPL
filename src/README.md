# Source

`pipeline.py` starts directly from CSP#, runs PAT verification, and uses failed
assertions plus counterexample traces to repair CSP# iteratively. The original
planning/code-generation pipeline is preserved at
`../archive/Automated_Pipelines/Full_Pipeline/pipeline.py`.

Place CSP# files in `../inputs/`, then run:

```powershell
python src/pipeline.py
```

Trace-guided repair is enabled by default. Copy `.env.example` to `.env` and
configure an OpenAI-compatible endpoint:

```env
LLM_BASE_URL=https://your-relay.example.com/v1
LLM_API_KEY=replace-with-your-api-key
LLM_MODEL_ID=replace-with-the-relay-model-id
```

It can be disabled for verification-only experiments:

```powershell
python src/pipeline.py --no-refine
```

One or more files can also be selected explicitly:

```powershell
python src/pipeline.py inputs/example.csp
```

Each invocation creates a `MMDD-HHMM-00X` directory under `../results/`.
Round `000` records the original verification. Each repair round records its
prompt, raw model response, candidate CSP#, isolated assertions, PAT output,
mismatch traces, and structured verification result. A candidate is rejected
if it changes the original `#assert` statements.

At present every input `#assert` is treated as expected `VALID`. This matches
the repair objective: repair model behavior until all supplied properties hold.
