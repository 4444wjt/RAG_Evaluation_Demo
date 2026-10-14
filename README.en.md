# VeriScope AI (鉴真全域)

**Every sentence of an AI answer, traceable to its source.**

[中文](README.md) · [Gate credibility (Chinese)](docs/competition/gate-credibility.md) · [Gate replay](docs/gate-replay.md) · [Deploy](deploy/README.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

VeriScope AI is a sentence-level verification and quality-gate platform for RAG (retrieval-augmented generation) question answering. It splits each model answer into atomic statements, checks every statement against the retrieved context, and lets a quality gate decide, by threshold, whether a batch of answers may ship. Test set, retrieved context, answers from several models, Ragas metrics, per-sentence verdicts, gate records and human review are kept in one chain, so "can this answer be trusted?" can be traced step by step: test set → sentence check → gate → report → human review.

Most project documents are written in Chinese; this page summarises them in English.

## Typical use: a regression gate in CI

Before a RAG application goes live, wire VeriScope AI into CI as a regression gate: if the gate blocks, the build fails. Archived evaluation results can be replayed offline — no model calls, no API keys — and a blocked verdict exits non-zero.

The gate is packaged as a GitHub composite action ([action.yml](action.yml)):

```yaml
- uses: actions/checkout@v4
- uses: 4444wjt/RAG_Evaluation_Demo@master   # pin to a release tag once one includes action.yml
  with:
    result: path/to/evaluation-result.json
    faith: "0.8"                             # optional, positive-sample faithfulness threshold
    ctx: "0.8"                               # optional, positive-sample context-precision threshold
    junit: quality-gate.junit.xml            # optional JUnit report
```

With a `baseline` (or `--baseline old.json`), the gate also compares this run with a previous one question by question. It bootstraps the mean change over paired questions and uses the measured same-judge re-scoring noise (0.0167 for faithfulness, 0.04 for context precision) as a noise floor: only a confirmed regression — 95% interval entirely below zero and a drop larger than judge noise — blocks the build; drops within sampling or judge noise are reported as warnings ([details](docs/gate-replay.md)).

The job summary shows which metric failed and by how much; the JUnit report can be published to any CI test viewer. Outputs: `passed` and `exit-code` (0 pass, 1 blocked, 2 invalid input). The same replay runs locally:

```bash
python scripts/gate_replay.py docs/competition/evidence/20261005T144504Z-full-39511424/api-response.json --junit gate.junit.xml
```

## What it does

| Stage | Capability |
|---|---|
| Test set | Document loading, Chroma retrieval, model answers and Ragas evaluation; generated, custom or TruthfulQA test sets |
| Sentence-level evidence | Original answer, atomic statements, verdicts (supported / factual error / unsupported) with the matching evidence and retrieved text |
| Quality gate | Uses the first valid model's positive-sample faithfulness and context precision; default thresholds 0.8 each; real FAILs are kept |
| Multi-model comparison | DeepSeek, Qwen2.5-7B, GLM-5.2-Fast on the same test set |
| Reports | CSV, HTML, Excel, PDF and charts |
| Human review | Human conclusions stored next to machine verdicts, never overwriting them, bound to a frozen snapshot by SHA-256 ([archive format](docs/review-archive-format.md)) |

**Interpretation limits.** PASS only means the sampled cases met the configured thresholds; it does not mean every metric is good or every answer is correct. Sentence verdicts depend on the original trace records and are not an independent guarantee of truth. Missing metrics are never filled with zero, and an empty trace does not mean "no hallucination". See [evaluation credibility](docs/evaluation-credibility.md) (Chinese).

## Built on, and built here

Metrics are not reinvented: they come from Ragas 0.1.21. This project works above the metric layer:

- **Built here:** sentence-level verdicts and evidence location (`backend/services/trace.py`); the quality gate (`backend/core/gate.py`); human-review archives; a Chinese-sentence compatibility layer `zh-sentence-v1` (`backend/adapters/ragas_compat.py`), which fixes Ragas 0.1.21 dropping Chinese sentences during statement splitting.
- **Reused:** Ragas; LangChain, Chroma and OpenAI-compatible APIs; React and ECharts; Docker Compose.

Threshold gates are not unique to this project (DeepEval, for example, can fail CI on metric thresholds). The difference is that the verdict sits next to sentence-level evidence that traces back to the retrieved text, together with human-review disagreements, all in one frozen snapshot — and that the gate itself is audited.

## Auditing the gate

A gate's PASS / FAIL drives a release decision, so the gate itself is checked ([details in Chinese](docs/competition/gate-credibility.md)). All numbers come from the 25-question formal run of 2026-10-05 (DeepSeek-V4.1-Flash: requested `deepseek-chat`, the server returned `deepseek-flash`; gate FAIL: positive faithfulness 0.7375 < 0.8, context precision 0.9833).

| Question | Result |
|---|---|
| Can the verdict be recomputed? | All 52 statement verdicts map back to the 20 positive questions; the FAIL is reproduced exactly |
| Does a different judge change it? | Re-judged by the same judge and by Qwen2.5-72B: FAIL all three times; per-question differences across judges are 5–6× the same-judge noise |
| Does the judge follow the evidence? | 6 fixed statements, evidence shuffled / removed / contradicted, 2 judges: 59 of 60 valid verdicts matched the expected labels; 1 parse failure kept for manual review |
| How certain is the FAIL? | Question-level bootstrap 95% interval for positive faithfulness: 55.0%–90.0%, straddling 0.8 — a conservative block, not a certain failure |
| Where did the FAIL come from? | Failure attribution (`backend/core/attribution.py`) splits the faithfulness gap by stage: all of it comes from 4 false-premise questions where retrieval found the evidence (recall 1.0) but the model refused instead of correcting the premise — a generation problem, not retrieval |
| Is the gate code itself correct? | 10 Hypothesis property tests (threshold boundaries, monotonicity, question order, negative samples, …); mutation testing with mutmut: 39 of 42 mutants killed, the 3 survivors do not affect the verdict |

The workbench's gate-audit view (门禁审计) shows these results together with a baseline-comparison example; its data is generated from the frozen evidence by `tools/competition/build_audit_fixture.py`, which checks every source hash and refuses to run if the gate code no longer matches the archived mutation run.

These results come from one single-domain run and are not a general benchmark or an overall accuracy claim.

## External benchmark: is the statement-level check itself accurate?

The platform's statement-level check (`backend/services/trace.py`) splits each answer into atomic statements and cites the supporting source text. (The gate score itself comes from Ragas faithfulness, a separate judgment by the same model with a different prompt.) On 2026-10-13 the statement-level check was tested against two public, human-annotated Chinese datasets, calling the production code unchanged (500 requests, one authorized run, about ¥2.68 by returned usage; [details in Chinese](docs/competition/external-validation/README.md)):

- **ANAH** (ACL 2024), 300 sentences with human hallucination labels, balanced by class:
  - recall on hallucinated sentences 0.89 (95% CI 0.84–0.93);
  - precision 0.87, about 0.72 once adjusted to the natural class ratio;
  - specificity 0.73. The check is stricter than the human annotators: summarising or evaluative sentences are often marked unsupported.
- **RGB** (AAAI 2024), 100 news questions:
  - with noisy context, 0 of 40 correct answers were flagged;
  - with irrelevant context, the model refused 87.5% of the time;
  - when the context itself is wrong, answers that copy the error are mostly judged supported (3 of 11 flagged). This is the inherent limit of a faithfulness check: it tests agreement with the evidence, not whether the evidence is true.

The archive keeps only item IDs and verdicts (no dataset text); the gate-audit view recomputes the summary from them and checks the hashes.

## Robustness check-up and judge-error calibration

**Robustness check-up** (`scripts/robustness_check.py`, view 鲁棒性体检): for any RAG application, the tool locates the supporting passages without a model and builds four evidence conditions — shuffled, noisy, support removed, counterfactual (numbers and modal words edited inside the evidence). It then checks both sides:

- the judge: the same answer is re-checked against the changed evidence;
- the application: it answers again from the changed evidence. The application can be called through a simple HTTP contract.

One authorized run on the 20 positive questions (260 requests, about ¥0.47) gave these results:

- Judge side: 57 of 58 checks met expectations; removed support was flagged 16 of 16 times.
- Application side: it refused 19 of 20 times when support was removed, and never kept the original value under counterfactual evidence.

See [details in Chinese](docs/competition/robustness/README.md).

**Judge-error calibration** (`backend/core/judge_error.py`, view 人工标注): the gate interval above treats every statement verdict as correct. The project now implements intervals that account for both question sampling and judge error:

- the calibration-based correction of Lee et al. (ICML 2026), extended to statements clustered by question through an effective sample size;
- prediction-powered inference (PPI) for partial annotation.

In a simulation with this run's question structure, the question-sampling-only interval covered the truth only 3%–53% of the time, while the clustered correction covered it 94%–96%. Only 5 answered statements were judged unsupported, and all 5 would have to flip to reach 0.80. Status: **the judge-error analysis tools and the simulation are done; the real human calibration is pending.** The judge's actual error rates require **independent human annotation by non-authors** (52 statements, about 30 minutes). The analysis protocol is fixed in advance: the gate's denominators are kept, unresolved statements are bounded rather than dropped, and partial annotation is used only for a consecutive prefix of at least 10 questions. Until the annotation is done, no corrected verdict is reported. See [details in Chinese](docs/competition/judge-calibration/README.md).

**What the platform adds:** for the same FAIL, the plain Ragas average is 0.59, because it counts 5 correct refusals on negative questions as 0. The platform reports 0.7375 on positive questions and explains it: all 40 statements in the answered questions are grounded; 76% of the gap comes from 4 refusals on false-premise questions, so the fix is the prompt, not retrieval; and the interval spans the threshold, so the block is conservative. See [details in Chinese](docs/competition/value-comparison.md).

**External case:** on 2026-10-14 the platform checked the default knowledge-base prompt of [Langchain-Chatchat](https://github.com/chatchat-space/Langchain-Chatchat) (authorized, 100 requests, about ¥0.15). Same model and same retrieved contexts; only the prompt changed. On the 5 false-premise questions, its prompt refused 2 and this project's own prompt refused 3. For two of them (F03, F04) the contexts contain the text needed to correct the premise, yet both prompts answered "cannot answer". Both prompts correctly refused all 5 unanswerable questions, and every answered statement was grounded. Only the default prompt was tested, not the project's real deployments. See [details in Chinese](docs/competition/cases/langchain-chatchat/README.md).

## Quick start

Python 3.11 and Node.js 24 are recommended.

**Demo only (no API keys):**

```bash
git clone https://github.com/4444wjt/RAG_Evaluation_Demo.git
cd RAG_Evaluation_Demo/frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/` and click the demo button. The front end loads a frozen snapshot (6 questions, 3 models, 18 answers) — demo data, not a business result.

**Static fallback on GitHub Pages:** https://4444wjt.github.io/RAG_Evaluation_Demo/ serves the same read-only snapshot as a static front end (no back end, no API calls), built by `.github/workflows/pages.yml`.

**Read-only demo site with Docker:** run `./deploy.sh` in `deploy/` ([deploy guide](deploy/README.md)). By default it listens on 127.0.0.1 only and rejects online evaluation (HTTP 423) at both nginx and the back end.

**Real evaluation:** install `requirements.txt` into a virtual environment, copy `.env.example` to `.env` and fill in provider keys (`OPENAI_API_KEY` for DeepSeek, `SILICONFLOW_API_KEY`, `DASHSCOPE_API_KEY`), prepare the local embedding model `BAAI/bge-small-zh-v1.5`, then start the API:

```bash
python -m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Keys stay in the local `.env` or back-end environment only. Real evaluation calls paid model APIs. Evaluation is a synchronous request; there is no task queue, cancellation or progress API yet.

## Tests and CI

```bash
python -m pip install -r tests/requirements.txt
python -m pytest -q tests
cd frontend && npm test && npm run typecheck
```

GitHub Actions runs the Python tests, the front-end unit tests with type checking, and a self-test of the gate action on pushes and pull requests to master; mutation testing of the gate runs when the gate code or its tests change.

## Limitations

- One domain: the formal run uses excerpts of a single Chinese financial regulation; results do not generalise to other domains. The statement-level check has been spot-checked on news (RGB), but the evaluation scenario itself is still single-domain.
- Same-source judge: by default the evaluated model also acts as the Ragas judge; the independent-judge comparison above quantifies, but does not remove, this bias.
- The 0.8 threshold is not yet calibrated against business outcomes.
- No authentication or access isolation; the public demo is read-only.
- Human review so far covers 13 targeted samples checked by the author, not an independent review.

Plans, target dates and the issue response commitment (first reply within 3 working days) are in the [roadmap](ROADMAP.md) (Chinese).

## License

The project's own code is released under the [MIT License](LICENSE). Third-party code and dependencies keep their own licenses; see [third-party notices](frontend/THIRD_PARTY_NOTICES.md). Citation metadata is in [CITATION.cff](CITATION.cff).
