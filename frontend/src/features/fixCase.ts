import fixData from '../fixtures/fix-verification.json';
import caseData from '../fixtures/fix-case.json';

// Both fixtures are generated from the archived runs (build_fix_fixture.py, build_fix_case.py) and never edited by hand.
// The guided tour and the downloadable review card read only from here, so a number or a quote has one source.
export const fix = fixData;
export const evidence = caseData;
export type CaseItem = (typeof caseData.cases)[number];
export type Statement = { text: string; verdict: string; evidence: string };
/** One run's answer and its statement-level trace, exactly as recorded; an empty trace stays empty. */
export type Side = { answer: string; trace: { status: string | null; statements: Statement[] } };

const formal = fixData.formal;
export const MAIN = caseData.cases.find((item) => item.role === 'main')!;
export const CONTRAST = caseData.cases.find((item) => item.role === 'contrast')!;
export const rowOf = (id: string) => formal.rows.find((row) => row.id === id)!;
export const RUNS = { before: caseData.sources.baseline, after: caseData.sources.fixed } as const;
export const runDate = (run: string) => run.replace(/^(\d{4})(\d{2})(\d{2})T.*$/, '$1-$2-$3');
export const MODEL = fixData.model;
export const MODEL_NOTE = '请求配置相同不等于实际返回身份相同；修复后 Trace 记录返回 deepseek-flash，基线 Trace 未记录返回身份。';
export const TRACE_NOTE = '下列逐句判定来自自研 Trace，supported 是原裁判记录；忠实度由 Ragas 另行计分，Trace 不参与门禁计算。';
export const HASH_NOTE = 'SHA-256 只绑定明确文件的字节，不证明内容语义正确，也不保证重新调用模型的输出可复现。';
export const SAMPLE = {
  total: formal.rows.length,
  positive: formal.rows.filter((row) => row.caseType === 'positive').length,
  negative: formal.rows.filter((row) => row.caseType === 'negative').length,
};
export const OTHER_CHANGED = fixData.code.changed.filter((path) => path !== 'backend/services/answering.py');
export const CORRECTED = formal.contribution.correctedIds;
export const HELDOUT_FP = fixData.heldout.groups.find((group) => group.id === 'false_premise')!;

export const VERDICT: Record<string, string> = { supported: '有依据', fact_error: '与资料矛盾', fabrication: '资料中找不到依据' };
export const NO_TRACE = '原记录无逐句判定';

/** Source links stay on the commit checked when the case was prepared, so moving a branch never moves the evidence. */
export const pinnedUrl = (path: string) => `${caseData.pinned.repository}/blob/${caseData.pinned.commit}/${path}`;
export const PINNED_TREE = `${caseData.pinned.repository}/tree/${caseData.pinned.commit}`;

/** Every archived file the tour quotes, with the hash it was checked against. */
export const SOURCE_FILES = (['before', 'after'] as const).flatMap((side) =>
  Object.entries(RUNS[side].sha256).map(([name, sha256]) => ({ side, path: `${RUNS[side].path}/${name}`, sha256, gitBlobSha256: (RUNS[side].gitBlobSha256 as Record<string, string>)[name] })));
export const sourceHashCommand = (path: string) => `sha256sum ${path}`;

const baselineResponse = `${RUNS.before.path}/api-response.json`;
const fixedResponse = `${RUNS.after.path}/api-response.json`;
/** Offline replay: no model call, no key. Expected outcomes are the archived ones, checked when the case was built. */
export const REPLAY = [
  { command: `python scripts/gate_replay.py ${baselineResponse}`, expect: '原运行：FAIL，退出码 1' },
  { command: `python scripts/gate_replay.py ${fixedResponse} --baseline ${baselineResponse}`, expect: '修复后：PASS，退出码 0' },
  { command: 'python tools/competition/build_fix_fixture.py --check', expect: '本页数字能否由三份归档重新算出' },
  { command: 'python tools/competition/build_fix_case.py --check', expect: '本页 F03、F04 原文能否由原始响应逐字提取' },
] as const;
export const REPLAY_TEXT = REPLAY.map((line) => `${line.command}  # ${line.expect}`).join('\n');

export const REVIEW_CARD_FILENAME = `veriscope-review-card-${MAIN.id}.md`;

const quote = (text: string) => `「${text}」`;
export const score = (value: number | null) => (value === null ? '—' : value.toFixed(2));
const trace = (item: Side) => item.trace.statements.length
  ? item.trace.statements.map((s: Statement, index: number) => `${index + 1}. ${s.text}\n   判定：${s.verdict}（${VERDICT[s.verdict] ?? s.verdict}）；裁判引用：${quote(s.evidence)}`).join('\n')
  : `${NO_TRACE}${item.trace.status ? `（trace_status：${item.trace.status}）` : ''}。`;
const gateLine = (gate: typeof formal.gate.before) => `${gate.passed ? 'PASS' : 'FAIL'}，正向忠实度 ${gate.posFaithfulness} ${gate.passed ? '≥' : '<'} ${formal.thresholds.faithfulness}`;

/** The one-page review card: it only arranges what is already archived and states that nothing was run. */
export function reviewCard(): string {
  const { before, after } = formal.gate;
  const main = rowOf(MAIN.id);
  const contrast = rowOf(CONTRAST.id);
  const rerun = fixData.judgeRerun;
  const marks = (item: CaseItem) => item.highlights.map((mark) => `${quote(mark.text)}（第 ${mark.context + 1} 段，登记依据 ${mark.referenceIds.join('、')}）`).join('；');
  return `# VeriScope 复核卡 · ${MAIN.id}「${MAIN.title}」

> **${caseData.purpose}。** 本卡只整理两次已归档运行的原始记录；本案例不发起模型评测，复核卡在浏览器本地生成；这不描述其他工作区功能的联网行为。

## 案例用途

沿一道真实题目看清四件事：门禁为什么拦下、依据在哪里、处理后发生了什么、怎样复算。${MAIN.id} 只是整批 ${SAMPLE.total} 题中的一个例子；${CONTRAST.id}「${CONTRAST.title}」作为对照保留。

## 运行编号

| | 运行 | 日期 | 门禁 |
|---|---|---|---|
| 修复前 | \`${RUNS.before.run}\` | ${runDate(RUNS.before.run)} | ${gateLine(before)} |
| 修复后 | \`${RUNS.after.run}\` | ${runDate(RUNS.after.run)} | ${gateLine(after)} |

回答与裁判的请求配置：\`${MODEL}\`。${MODEL_NOTE}资料：\`${formal.request.docPath}\`；题集：\`${formal.request.testsetPath}\`，${SAMPLE.total} 题，其中正向 ${SAMPLE.positive} 题计入门禁，资料无答案 ${SAMPLE.negative} 题不计入门禁。

## ${MAIN.id} 修复前后

问题：${MAIN.question}

参考答案：${MAIN.groundTruth}

同次检索到的原文：两次运行同为 ${MAIN.contexts.length} 段、逐字相同。其中与交纳保费主体有关的原文：${marks(MAIN)}。

**修复前（${runDate(RUNS.before.run)}）** 忠实度 ${score(main.before.faithfulness)}

> ${MAIN.before.answer}

${trace(MAIN.before)}

**修复后（${runDate(RUNS.after.run)}）** 忠实度 ${score(main.after.faithfulness)}

> ${MAIN.after.answer}

原始逐句判定 ${MAIN.after.trace.statements.length} 条：

${TRACE_NOTE}

${trace(MAIN.after)}

## ${CONTRAST.id} 未解决

问题：${CONTRAST.question}

两次回答都是：

> ${CONTRAST.after.answer}

修复前：${trace(CONTRAST.before)} 修复后：${trace(CONTRAST.after)} 两次忠实度均为 ${score(contrast.after.faithfulness)}。它的检索资料里有 ${marks(CONTRAST)}，模型仍然拒答；本次没有继续调整。

## 处理

提交 \`${fixData.fix.commit}\` 在问答提示词（\`backend/services/answering.py\`）中新增一行：

> ${fixData.fix.line}

## 两次门禁

| 指标 | 修复前 | 修复后 | 门槛 |
|---|---|---|---|
| 正向忠实度 | ${before.posFaithfulness} | ${after.posFaithfulness} | ≥ ${formal.thresholds.faithfulness} |
| 正向上下文精度 | ${before.posContextPrecision} | ${after.posContextPrecision} | ≥ ${formal.thresholds.contextPrecision} |
| 门禁结论 | ${before.passed ? 'PASS' : 'FAIL'} | ${after.passed ? 'PASS' : 'FAIL'} | |
| 95% 按题重抽区间 | ${before.certainty.low}–${before.certainty.high}（${before.certainty.state}） | ${after.certainty.low}–${after.certainty.high}（${after.certainty.state}） | |

两次区间都跨过 ${formal.thresholds.faithfulness}：门禁照规则放行，不等于确定达标。

## 来源与哈希

固定提交：${PINNED_TREE}

| 文件 | 归档检出 SHA-256 | 固定链接原始文件 SHA-256 |
|---|---|---|
${SOURCE_FILES.map((file) => `| \`${file.path}\` | \`${file.sha256}\` | \`${file.gitBlobSha256}\` |`).join('\n')}

归档检出哈希与各归档的 \`SHA256SUMS.txt\` 一致。基线 analysis.json 检出为 CRLF，固定链接原始文件为 LF，只有换行不同；直接下载链接原始文件时，核对右列哈希。两份 API 响应的两列哈希相同。

${HASH_NOTE}

在仓库根目录对实际检出文件执行，按对应字节版本核对上表：

\`\`\`bash
${SOURCE_FILES.map((file) => sourceHashCommand(file.path)).join('\n')}
\`\`\`

复核卡下载为 UTF-8 Markdown。保存后在其所在目录运行 \`sha256sum ${REVIEW_CARD_FILENAME}\`，与网页显示的复核卡文件 SHA-256 核对。

## 复算命令

在仓库根目录离线运行，不调用模型、不需要密钥：

\`\`\`bash
${REPLAY_TEXT}
\`\`\`

前三条在固定提交上即可运行；最后一条需要包含 \`tools/competition/build_fix_case.py\` 的版本。

## 适用边界

- ${caseData.purpose}：没有新的模型调用，也没有改动门禁、阈值或任何已记录的判定。
- ${MAIN.id} 只是一例：同批 ${CORRECTED.join('、')} 由拒答改为纠正前提，${CONTRAST.id} 仍拒答。
- 两次 ${SAMPLE.total} 题运行之间另有 ${OTHER_CHANGED.length} 个源文件不同，不能当作严格的单因素实验。严格对照见留出题：同一请求配置、只差这一句，错误前提题 ${HELDOUT_FP.passBefore}/${HELDOUT_FP.questions} → ${HELDOUT_FP.passAfter}/${HELDOUT_FP.questions}，符号检验 p = ${HELDOUT_FP.signTestP}，未达 0.05。[留出题记录](${pinnedUrl(`${fixData.source.heldout.path}/README.md`)})。
- 换 ${rerun.judgeModel} 作裁判重跑修复版：正向忠实度 ${rerun.gate.posFaithfulness}，门禁 ${rerun.gate.passed ? 'PASS' : 'FAIL'}；该次重跑也重新生成了回答，${SAMPLE.total} 题中 ${rerun.sameAnswers} 题与修复验证重跑逐字相同。[换裁判记录](${pinnedUrl(`${fixData.source.judge.path}/README.md`)})。
- 一个领域、一套请求模型配置、各跑一次；「提示词只给了拒答一个出口」的原因分析尚未人工确认。
`;
}
