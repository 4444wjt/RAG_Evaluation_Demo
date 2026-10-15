<a href="https://kk444444444444.cloud/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/readme-banner-dark.gif">
    <img src="docs/images/readme-banner.gif" alt="VeriScope AI — sentence verification, evidence tracing and quality gates" width="1400" height="560">
  </picture>
</a>

[Static cover](docs/images/readme-banner.png) · [Dark cover](docs/images/readme-banner-dark.png)

<a href="CHANGELOG.md"><img src="docs/images/badge-version.png" alt="version v1.2.0" height="20"></a> <a href="#quick-start"><img src="docs/images/badge-platform.png" alt="platform Python 3.11+" height="20"></a> <a href="LICENSE"><img src="docs/images/badge-license.png" alt="license MIT" height="20"></a> <a href="https://github.com/4444wjt/RAG_Evaluation_Demo/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/4444wjt/RAG_Evaluation_Demo/ci.yml?label=CI" alt="CI"></a> <a href="https://github.com/4444wjt/RAG_Evaluation_Demo/actions/workflows/codeql.yml"><img src="https://img.shields.io/github/actions/workflow/status/4444wjt/RAG_Evaluation_Demo/codeql.yml?label=CodeQL" alt="CodeQL"></a>

# VeriScope AI (鉴真全域)

**Every sentence of an AI answer, traceable to its source.**

Repositories: [Gitee (primary)](https://gitee.com/kkbushishou/rag-hallucination-evaluator) · [GitHub (mirror, CI and Pages fallback)](https://github.com/4444wjt/RAG_Evaluation_Demo)

[中文](README.md) · [Gate credibility (Chinese)](docs/competition/gate-credibility.md) · [Gate replay](docs/gate-replay.md) · [Deploy](deploy/README.md) · [Adoption & integration (Chinese)](docs/DEPLOYMENT.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

VeriScope AI is a sentence-level verification and quality-gate platform for RAG (retrieval-augmented generation) question answering. It splits each model answer into atomic statements, checks every statement against the retrieved context, and lets a quality gate decide, by threshold, whether a batch of answers may ship. Test set, retrieved context, answers from several models, Ragas metrics, per-sentence verdicts, gate records and human review are kept in one chain, so "can this answer be trusted?" can be traced step by step: test set → sentence check → gate → report → human review.

Most project documents are written in Chinese; this page summarises them in English.

**Judge configuration and external applications:** the workbench, API and CLI support a [separately configured judge](docs/integrations/independent-judge.md), used for all five metrics and Trace while the tested model generates the answer. Requested and observed judge identities are recorded separately. The [external RAG end-to-end tool](docs/integrations/external-rag.md) sends only an ID and question; the application returns its answer and the actual contexts used for generation. Runs have separate archives. Each was run once for real on 2026-10-14 (authorized): a [25-question rerun with an independent judge](docs/competition/evidence/20261014T125415Z-full-a5002d4f/README.md) and an [end-to-end run against a self-written example app](docs/competition/external-rag/20261014-example-app-qwen72b/README.md), both in Chinese. Both gates FAIL; see "With an independent judge" below. Historical results are unchanged.

## Where it fits

| Form | In one sentence | How to adopt |
|---|---|---|
| Stand-alone QA platform | Evaluate a RAG application before release: sentence-level checks, model comparison, gate verdict and reports | Local workbench, or [Docker Compose](deploy/README.md) on an internal network |
| Embedded in an existing system | Call the sentence check and gate from your own platform, or point the platform at your RAG application's endpoint | HTTP API, Python function, `--generator-endpoint` ([integration guide](docs/DEPLOYMENT.md), Chinese) |
| CI gate | Re-check after every prompt, model or retrieval change; a blocked gate fails the build | GitHub Action, below |

Your own models (any OpenAI-compatible endpoint: vLLM, Ollama, an internal gateway) can be registered on the workbench's model-configuration page; keys stay in the local `.env`. The integration guide also lists what has and has not been verified for private or air-gapped deployment.

### Target industries

The platform blocks a release that falls short, before launch and after every change; it does not filter answers one by one in production. It only checks whether answers agree with the source documents, so it suits industries where those documents are authoritative, wrong answers are costly, decisions must be traceable, and data must stay on premises.

**Banking and insurance terms.** Testers of customer-service bots and staff assistants at banks and insurers use it before launch and after every model or prompt change. Without the gate, a wrong payout limit, enrolment deadline or exclusion clause, or an answer that goes along with a customer's mistaken premise, reaches customers as the institution's word, leading to complaints and claim disputes with no way to tell which sentence came from which clause. The platform checks every sentence against the clause text and fails the gate, and the CI step, when positive faithfulness or context precision falls below the threshold: the 25-question run on an excerpt of the Deposit Insurance Regulations was blocked this way (0.7375 < 0.8), traced to refused false-premise questions, and passed at 0.8607 after a one-sentence prompt fix ([formal run](docs/competition/evidence/20261005T144504Z-full-39511424/README.md), [fix rerun](docs/competition/evidence/20261014T073859Z-full-1c6f091f/README.md), in Chinese).

**Government service enquiries.** Teams that build government Q&A services and hotline knowledge bases use it at delivery acceptance and whenever a policy document is revised. Without the gate, wrong application deadlines, response periods or required documents, or a bare "cannot answer" to a question built on a false premise, leave citizens missing deadlines or making wasted trips, which turn into complaints and administrative appeals. The platform checks every sentence against the regulation text and fails the gate when faithfulness or context precision is too low, and held-out questions with false premises, true premises and no answer in the source show whether a change overcorrects: on an excerpt of the Open Government Information Regulations that played no part in debugging, all 14 held-out questions passed after the fix, up from 12 ([held-out check](docs/competition/heldout-fix/20261014-deepseek/README.md), in Chinese).

**State-owned enterprise policies.** Testers of policy assistants at group IT subsidiaries and information centres use it before launch and after each policy revision, with compliance and internal-audit staff reviewing the results. Without the gate, wrong purchase limits, approval authorities or expense rules are followed by staff, and when internal audit finds the violation, the person who acted on the answer is held responsible. The platform and your own models can run on the internal network so documents stay inside; every sentence is checked against the policy text, a failed gate stops the release, and the human-review page lets reviewers confirm each verdict, stored separately from the machine's (not yet tested on documents of this kind in this repository).

**Not a fit:** news and web-search Q&A (the sources are not guaranteed correct); marketing copy, creative writing and chit-chat (no source to check against); investment advice and medical diagnosis (the conclusion is not in the documents); anything that needs answers blocked one by one in real time.

## Typical use: a regression gate in CI

Before a RAG application goes live, wire VeriScope AI into CI as a regression gate: if the gate blocks, the build fails. Archived evaluation results can be replayed offline — no model calls, no API keys — and a blocked verdict exits non-zero.

The gate is packaged as a GitHub composite action ([action.yml](action.yml)), hosted in the GitHub mirror. Keep the `uses: 4444wjt/RAG_Evaluation_Demo@master` reference pointing there:

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
| Multi-model comparison | Built-in DeepSeek, Qwen2.5-7B, GLM-5.2-Fast, plus any OpenAI-compatible model registered locally, on the same test set |
| Reports | CSV, HTML, Excel, PDF and charts |
| Human review | Human conclusions stored next to machine verdicts, never overwriting them, bound to a frozen snapshot by SHA-256 ([archive format](docs/review-archive-format.md)) |

**Interpretation limits.** The sentence check has three layers that do not substitute for one another: **citation located** (the quoted evidence appears verbatim in the retrieved text, checked by code), **semantic support** (the statement follows from that text, judged by a model and subject to error), and **factual correctness** (whether the statement is true in the world, which the platform does not check). PASS only means the sampled cases met the configured thresholds; it does not mean every metric is good or every answer is correct. Missing metrics are never filled with zero, and an empty trace does not mean "no hallucination". See [evaluation credibility](docs/evaluation-credibility.md) (Chinese).

## Built on, and built here

Metrics are not reinvented: they come from Ragas 0.1.21. This project works above the metric layer:

- **Built here:** sentence-level verdicts and evidence location (`backend/services/trace.py`); the quality gate (`backend/core/gate.py`); human-review archives; a Chinese-sentence compatibility layer `zh-sentence-v1` (`backend/adapters/ragas_compat.py`), which fixes Ragas 0.1.21 dropping Chinese sentences during statement splitting.
- **Reused:** Ragas; LangChain, Chroma and OpenAI-compatible APIs; React and ECharts; Docker Compose.

Neither threshold gates nor statement-level checking is unique to this project. [DeepEval](https://github.com/confident-ai/deepeval) can fail CI on metric thresholds. [RAGChecker](https://github.com/amazon-science/RAGChecker) (NeurIPS 2024) also splits answers into claims, checks each one, and reports separate diagnostics for retrieval and generation; it is the closest prior work on statement-level checking. [TruLens](https://www.trulens.org/getting_started/core_concepts/rag_triad/) checks groundedness claim by claim, and [Phoenix](https://github.com/Arize-ai/phoenix) offers tracing and experiments with separate faithfulness and correctness evaluators. The difference here is the combination: the verdict sits next to sentence-level evidence that traces back to the retrieved text, together with human-review disagreements, all in one frozen snapshot — and the gate itself is audited. This compares stated features from public material (checked up to 2026-10-14), not performance under the same conditions; no claim is made that this project checks more accurately.

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

In a simulation with this run's question structure, the question-sampling-only interval covered the truth only 3%–53% of the time, while the clustered correction covered it 94%–96%. Only 5 answered statements were judged unsupported, and all 5 would have to flip to reach 0.80. Status: **one non-author annotated all 52 statements blind on 2026-10-14.** On the human scale, positive faithfulness lies between **77.5% and 82.5%** (the judge gave 73.75%), straddling the 80% threshold. The difference turns on one statement, the "no answer in the document" refusal on F03, which the annotator marked as unsure. Under the protocol fixed in advance, an unresolved statement means no interval and no verdict; the recorded FAIL stands. The judge's sensitivity is 95.6% (43 / 45) and its specificity 100% (6 / 6, a small sample); both disagreements are cases where the judge was stricter. With a single annotator there is no agreement measure. See the [annotation result](docs/competition/judge-calibration/20261014-annotation-V/README.md), in Chinese. See [details in Chinese](docs/competition/judge-calibration/README.md).

**What the platform adds:** for the same FAIL, the plain Ragas average is 0.59, because it counts 5 correct refusals on negative questions as 0. The platform reports 0.7375 on positive questions and explains it: all 40 statements in the answered questions are grounded; 76% of the gap comes from 4 refusals on false-premise questions, so the fix is the prompt, not retrieval; and the interval spans the threshold, so the block is conservative. See [details in Chinese](docs/competition/value-comparison.md).

**Fix verified:** following that attribution, on 2026-10-14 one sentence was added to the answer prompt: when the question's claim or premise contradicts the context, point it out and correct it from the context instead of refusing (commit `b90a14c`). The 25 questions were then rerun with the same question set, contexts and model (authorized, 325 calls). The gate, thresholds and judge were not changed.
- Positive faithfulness went from 0.7375 to **0.8607**, and the gate went from **FAIL to PASS**.
- Of the 4 refused false-premise questions, 3 are now corrected; F04 is still refused.
- All 5 unanswerable questions are still correctly refused.
- A per-question regression check against the original FAIL found no regression.

**Held-out check:** the rerun above used the same questions that revealed the problem. To test whether the fix carries over, 24 new questions that played no part in the debugging were written, confirmed by the author, and run (authorized, 96 calls). The two arms differ only by the added sentence:
- False-premise questions: 9/14 → **14/14**. 5 went from refusal to a grounded correction, and none got worse.
- True-premise questions: 4/5 → 5/5. The fix did not "correct" statements that were already right.
- All 5 unanswerable questions are still correctly refused.

Limits: the sample is small; the paired sign test on false-premise questions gives p = 0.0625, which is not below 0.05. Both checks used one model (DeepSeek-V4.1-Flash, with the historical request ID `deepseek-chat`) and a single run each, and the 25-question rerun is only about 0.06 above the threshold. See details in Chinese: [fix rerun](docs/competition/evidence/20261014T073859Z-full-1c6f091f/README.md), [held-out check](docs/competition/heldout-fix/20261014-deepseek/README.md).

**With an independent judge:** the PASS above was scored by DeepSeek itself. The same fixed version was rerun on the same 25 questions with DeepSeek still generating and Qwen2.5-72B as judge (authorized). Positive faithfulness was **0.7992**, and the gate went back to **FAIL**.
- On the 13 positive questions with identical questions, retrieved contexts, reference answers and generated answers, the mean score fell from 0.9377 to 0.8718. Judge outputs differ for fixed inputs, but a single reassessment cannot separate judge selection from judgment randomness.
- Seven answers changed across the two batches, so the overall score change cannot be attributed entirely to the judge. The repaired version's PASS alone does not establish reliable gate passage. Both bootstrap intervals span 0.8, leaving limited evidence of stability in this small sample; the recorded PASS and FAIL are both retained.
- Two independently computed checks give inconsistent signals: on D10, F02 and others every statement was judged supported, yet Faithfulness was only 0.2–0.75. This difference alone does not establish a self-contradictory judge or a computation error; its cause remains unconfirmed.

See [independent-judge rerun](docs/competition/evidence/20261014T125415Z-full-a5002d4f/README.md) (in Chinese). The held-out 9/14 → 14/14 is based on refusals and on whether the sentence check found a factual error, not on faithfulness scores; that sentence check was also done by DeepSeek and has not been rerun with another judge.

**External case:** on 2026-10-14 the platform checked the default knowledge-base prompt of [Langchain-Chatchat](https://github.com/chatchat-space/Langchain-Chatchat) (authorized, 100 requests, about ¥0.15). Same model and same retrieved contexts; only the prompt changed. On the 5 false-premise questions, its prompt refused 2 and this project's own prompt refused 3. For two of them (F03, F04) the contexts contain the text needed to correct the premise, yet both prompts answered "cannot answer". Both prompts correctly refused all 5 unanswerable questions, and every answered statement was grounded. Only the default prompt was tested, not the project's real deployments. See [details in Chinese](docs/competition/cases/langchain-chatchat/README.md).

## Quick start

Python 3.11 and Node.js 24 are recommended.

**Demo only (no API keys):**

Clone from the primary Gitee repository:

```bash
git clone https://gitee.com/kkbushishou/rag-hallucination-evaluator.git RAG-Reliability-Lab
```

Alternatively, clone from the GitHub mirror; choose one of the two clone commands:

```bash
git clone https://github.com/4444wjt/RAG_Evaluation_Demo.git RAG-Reliability-Lab
```

Then start the front end:

```bash
cd RAG-Reliability-Lab/frontend
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

Gitee does not run GitHub Actions. View CI, CodeQL and Scorecard runs on the [GitHub mirror's Actions page](https://github.com/4444wjt/RAG_Evaluation_Demo/actions).

On pushes and pull requests to master/main in the GitHub mirror, GitHub Actions runs the Python tests, the front-end unit tests with type checking, and a self-test of the gate action; mutation testing of the gate runs when the gate code or its tests change.

## Limitations

- One domain: the formal run uses excerpts of a single Chinese financial regulation; results do not generalise to other domains. The statement-level check has been spot-checked on news (RGB), but the evaluation scenario itself is still single-domain.
- Same-source judge: by default the evaluated model also acts as the Ragas judge; the independent-judge comparison above quantifies, but does not remove, this bias.
- The 0.8 threshold is not yet calibrated against business outcomes.
- No authentication or access isolation; the public demo is read-only.
- Human review so far covers 13 targeted samples checked by the author, not an independent review.

Plans, target dates and the issue response commitment (first reply within 3 working days) are in the [roadmap](ROADMAP.md) (Chinese).

## License and repositories

The project's own code is released under the [MIT License](LICENSE). Third-party code and dependencies keep their own licenses; see [third-party notices](frontend/THIRD_PARTY_NOTICES.md). Citation metadata is in [CITATION.cff](CITATION.cff).

- Primary repository: [Gitee — kkbushishou/rag-hallucination-evaluator](https://gitee.com/kkbushishou/rag-hallucination-evaluator), maintainer remote `origin`.
- Mirror: [GitHub — 4444wjt/RAG_Evaluation_Demo](https://github.com/4444wjt/RAG_Evaluation_Demo), maintainer remote `github`; hosts CI, the GitHub Pages fallback and the gate Action.
