import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EvaluationResult, ResultRow } from '../api/types';
import { TraceView } from '../features/TraceView';
import { ReportView } from '../features/ReportView';

const localHtmlPath = 'D:/local/report.html';
const localChartPath = 'D:/local/chart.png';
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
const fetchMock = vi.fn<typeof fetch>();

function resultWith(models: Record<string, ResultRow[]> = {}): EvaluationResult {
  return {
    summary: [], summary_df: [], gate_passed: true, gate_message: '',
    chart_path: localChartPath, html_path: localHtmlPath,
    model_dfs: models, total_elapsed_seconds: null,
  };
}

function rowWith(trace: ResultRow['hallucination_trace'], extra: Partial<ResultRow> = {}): ResultRow {
  return { question: '原始测试问题', answer: '原始模型回答', contexts: '原始上下文', hallucination_trace: trace, ...extra };
}

function textFile(name: string, text: () => Promise<string>): File {
  const file = new File([], name, { type: 'text/html' });
  Object.defineProperty(file, 'text', { configurable: true, value: text });
  return file;
}

function chooseReport(file: File) {
  fireEvent.change(screen.getByLabelText('选择本地 HTML 报告'), { target: { files: [file] } });
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockRejectedValue(new Error('No network is permitted in detail tests.'));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
  else Reflect.deleteProperty(navigator, 'clipboard');
});

describe('TraceView 原始溯源', () => {
  it('没有结果时提供载入方向，不制造用例', () => {
    render(<TraceView result={null} />);
    expect(screen.getByRole('heading', { name: '暂无用例结果' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('缺少 Trace 字段时说明处理原因未记录', () => {
    render(<TraceView result={resultWith({ model: [rowWith('')] })} />);
    expect(screen.getByRole('heading', { name: '证据链信息未保留' })).toBeInTheDocument();
    expect(screen.getByText('该条记录未保存逐句证据链，无法据此判断回答是否有依据。')).toBeInTheDocument();
  });

  it('旧记录只有空数组时不冒充纯拒答或生成失败', () => {
    render(<TraceView result={resultWith({ model: [rowWith(JSON.stringify({ statements: [] }))] })} />);
    expect(screen.getByRole('heading', { name: '证据链信息未保留' })).toBeInTheDocument();
    expect(screen.getByText('该条记录只保存了空陈述列表，未保存生成、解析或过滤阶段；无法据此判断回答是否有依据。')).toBeInTheDocument();
    expect(screen.queryByText(/零幻觉|无幻觉|0%|0 个幻觉/)).not.toBeInTheDocument();
  });

  it('纯拒答显示无需验证事实陈述', () => {
    const trace = JSON.stringify({ statements: [], trace_status: 'empty_answer', trace_attempts: 1, trace_diagnostics: { answer_mode: 'refusal' } });
    render(<TraceView result={resultWith({ model: [{ ...rowWith(trace), answer: '根据提供的文档，无法回答该问题。', case_type: 'negative' }] })} />);
    expect(screen.getByRole('status', { name: '可靠性判断：正确拒答' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '该回答未产生需要验证的事实陈述' })).toBeInTheDocument();
    expect(screen.getByText('该回答是纯拒答，未产生需要验证的事实陈述。')).toBeInTheDocument();
  });

  it('损坏 trace 明确显示解析状态，不把失败当成空回答', () => {
    render(<TraceView result={resultWith({ model: [rowWith('{broken-json')] })} />);
    expect(screen.getByRole('heading', { name: 'Trace生成异常' })).toBeInTheDocument();
    expect(screen.getByText('Trace生成异常，请检查原始记录。')).toBeInTheDocument();
  });

  it('显示模型返回空结果的处理原因和调用次数', () => {
    const trace = JSON.stringify({ statements: [], trace_status: 'model_empty', trace_attempts: 1, trace_diagnostics: { answer_mode: 'normal' } });
    render(<TraceView result={resultWith({ model: [rowWith(trace)] })} />);
    expect(screen.getByRole('status', { name: '可靠性判断：需要检查' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '回答已生成，但逐句证据链未生成' })).toBeInTheDocument();
    expect(screen.getByText('回答已生成，但逐句证据链未生成，无法建立回答与检索依据的对应关系。')).toBeInTheDocument();
    expect(screen.getByText('Trace 尝试 1 次')).toBeInTheDocument();
  });

  it('把回答状态、证据链状态和可靠性判断三层独立展示', () => {
    const trace = JSON.stringify({
      statements: [{ text: '星语具备安全性', verdict: 'supported', evidence: '产品具备安全性' }],
      trace_status: 'ok',
      trace_diagnostics: { answer_mode: 'normal' },
    });
    render(<TraceView result={resultWith({ model: [{ ...rowWith(trace), answer: '星语具备安全性。', contexts: ['产品具备安全性'] }] })} />);
    expect(screen.getByText('正常回答', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('证据链已生成', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('可验证', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('有依据')).toBeInTheDocument();
    expect(screen.getByText('引用已定位')).toBeInTheDocument();
    expect(screen.getByText('机器逐句判定，不代表人工认证；引用已定位也不等于语义支持。')).toBeInTheDocument();
  });

  it('没有回答类型元数据时明确显示未记录，不从旧记录猜测', () => {
    const trace = JSON.stringify({ statements: [{ text: '历史陈述', verdict: 'supported', evidence: '历史证据' }] });
    render(<TraceView result={resultWith({ model: [{ ...rowWith(trace), contexts: ['历史证据'] }] })} />);
    expect(screen.getByText('回答类型未记录', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText(/历史记录没有可靠的回答类型元数据/)).toBeInTheDocument();
  });

  it('可靠性判断层按优先级区分状态并标注方向', () => {
    const evidenceReady = JSON.stringify({ statements: [{ text: '有依据', verdict: 'supported', evidence: '原文证据' }], trace_status: 'ok', trace_diagnostics: { answer_mode: 'normal' } });
    const unsupported = JSON.stringify({ statements: [{ text: '无依据', verdict: 'fabrication', evidence: '' }], trace_status: 'ok', trace_diagnostics: { answer_mode: 'normal' } });
    const refusal = JSON.stringify({ statements: [], trace_status: 'empty_answer', trace_diagnostics: { answer_mode: 'refusal' } });
    const unexpectedRefusal = JSON.stringify({ statements: [], trace_status: 'empty_answer', trace_diagnostics: { answer_mode: 'refusal' } });
    const noTrace = JSON.stringify({ statements: [], trace_status: 'model_empty', trace_diagnostics: { answer_mode: 'normal' } });
    const traceError = '{broken-json';
    const legacy = JSON.stringify({ statements: [] });
    const result = resultWith({ model: [
      { ...rowWith(evidenceReady), contexts: ['原文证据'] },
      { ...rowWith(unsupported), contexts: [] },
      { ...rowWith(refusal), answer: '根据材料无法回答。', case_type: 'negative' },
      { ...rowWith(unexpectedRefusal), answer: '根据材料无法回答。', case_type: 'positive' },
      { ...rowWith(noTrace), answer: '正常回答。' },
      { ...rowWith(traceError), answer: '异常回答。' },
      { ...rowWith(legacy), answer: '历史回答。' },
    ] });
    render(<TraceView result={result} />);
    const verify = (index: number, label: string, direction: string) => {
      if (index > 0) fireEvent.click(screen.getAllByRole('button', { name: /原始测试问题/ })[index]);
      const status = screen.getByRole('status', { name: `可靠性判断：${label}` });
      expect(within(status).getByText(direction)).toBeInTheDocument();
    };
    verify(0, '可验证', '利好');
    verify(1, '存在风险', '模型风险');
    verify(2, '正确拒答', '利好');
    verify(3, '异常拒答', '待复核');
    verify(4, '需要检查', '待复核');
    verify(5, '评测链路异常', '链路异常');
    verify(6, '信息不足', '无法评估');
  });

  it('区分候选陈述被过滤与模型返回空数组两种未生成原因', () => {
    const filteredAll = JSON.stringify({
      statements: [],
      trace_status: 'filtered_all',
      trace_attempts: 1,
      trace_diagnostics: {
        answer_mode: 'normal',
        input_statement_count: 3,
        rejected_statements: [{ index: 0, reason: 'evidence_not_in_context' }],
      },
    });
    const modelEmpty = JSON.stringify({ statements: [], trace_status: 'model_empty', trace_attempts: 1, trace_diagnostics: { answer_mode: 'normal' } });
    const result = resultWith({ model: [
      { ...rowWith(filteredAll), answer: '正常回答，但候选陈述被过滤。' },
      { ...rowWith(modelEmpty), answer: '正常回答，模型返回空数组。' },
    ] });
    render(<TraceView result={result} />);
    // 第一个问题：候选全部被过滤，证据链状态文案需区分
    expect(screen.getByText('证据链未生成（候选未通过校验）', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('模型返回了候选陈述，但全部未通过原文校验而被过滤，无法建立回答与检索依据的逐句对应关系。')).toBeInTheDocument();
    expect(screen.getByRole('status', { name: '可靠性判断：需要检查' })).toBeInTheDocument();
    // 切换到第二个问题：模型返回空数组，仍归入通用的「证据链未生成」
    fireEvent.click(screen.getAllByRole('button', { name: /原始测试问题/ })[1]);
    expect(screen.getByText('证据链未生成', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: '可靠性判断：需要检查' })).toBeInTheDocument();
  });

  it('区分拒答说明和前提纠正的回答类型', () => {
    const refusal = JSON.stringify({ statements: [], trace_status: 'model_empty', trace_diagnostics: { answer_mode: 'refusal_explanation' } });
    const correction = JSON.stringify({ statements: [], trace_status: 'model_empty', trace_diagnostics: { answer_mode: 'premise_correction' } });
    const result = resultWith({ model: [
      { ...rowWith(refusal), answer: '根据材料无法回答，因为文档未提及。' },
      { ...rowWith(correction), answer: '问题前提不成立，原文显示产品有两项特性。' },
    ] });
    render(<TraceView result={result} />);
    expect(screen.getByText('拒答说明', { selector: 'strong' })).toBeInTheDocument();
    expect(screen.getByText('这是拒答结果；其中的原因说明不等同于普通业务事实，逐句判定仍需结合原始上下文阅读。')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: /原始测试问题/ })[1]);
    expect(screen.getByText('前提纠正', { selector: 'strong' })).toBeInTheDocument();
  });

  it('保持模型与问题原序，模型切换后显示该模型首个问题', () => {
    const result = resultWith({
      'z-first-model': [{ ...rowWith(null), question: '第一模型第一个问题', answer: '第一模型首答' }, { ...rowWith(null), question: '第一模型第二个问题', answer: '第一模型次答' }],
      'a-second-model': [{ ...rowWith(null), question: '第二模型第一个问题', answer: '第二模型首答' }],
    });
    render(<TraceView result={result} />);
    expect(screen.getAllByRole('option').map((option) => option.getAttribute('value'))).toEqual(['z-first-model', 'a-second-model']);
    fireEvent.click(screen.getByRole('button', { name: /第一模型第二个问题/ }));
    expect(screen.getByText('第一模型次答')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'a-second-model' } });
    expect(screen.getByText('第二模型首答')).toBeInTheDocument();
    expect(screen.queryByText('第一模型次答')).not.toBeInTheDocument();
  });

  it('逐条展示原判定，并为所有带引用的陈述标记真实上下文片段', () => {
    const trace = { statements: [
      { text: '有依据断言', verdict: 'supported', evidence: '第二段原始证据' },
      { text: '错误断言', verdict: 'fact_error', evidence: '第一段原始证据' },
      { text: '编造断言', verdict: 'fabrication', evidence: '' },
      { text: '未知断言', verdict: 'new_verdict', evidence: '' },
      { text: '未匹配断言', verdict: 'supported', evidence: '不在上下文中的文字' },
    ] };
    render(<TraceView result={resultWith({ model: [{ ...rowWith(JSON.stringify(trace)), contexts: ['第一段原始证据', '第二段原始证据及后续原文'] }] })} />);
    expect(screen.getAllByText('有依据')).toHaveLength(2);
    expect(screen.getByText('事实错误')).toBeInTheDocument();
    expect(screen.getByText('无依据编造')).toBeInTheDocument();
    expect(screen.getByText('未知判定')).toBeInTheDocument();
    expect(screen.getByText('原始判定值：new_verdict')).toBeInTheDocument();
    expect(screen.getByText('精确匹配检索片段 2')).toBeInTheDocument();
    expect(screen.getByText('精确匹配检索片段 1')).toBeInTheDocument();
    expect(screen.getByText('未在当前检索上下文中找到精确匹配。')).toBeInTheDocument();
    const summary = screen.getByText('检索片段 2').closest('summary')!;
    fireEvent.click(summary);
    expect(screen.getByText('第二段原始证据及后续原文', { selector: 'p' })).toBeInTheDocument();
  });

  it('CSV 上下文作为一个原文显示，回答和证据中的 HTML 均被转义', () => {
    const markup = '<img src="https://never-load.invalid/x" onerror="globalThis.traceExecuted=true">';
    const rawContext = `['${markup}'\n '另一原段']`;
    const trace = JSON.stringify({ statements: [{ text: markup, verdict: 'supported', evidence: markup }] });
    const { container } = render(<TraceView result={resultWith({ model: [{ ...rowWith(trace), answer: markup, contexts: rawContext }] })} />);
    expect(screen.getAllByText(markup)).toHaveLength(3);
    expect(screen.getByText('上下文原始文本')).toBeInTheDocument();
    expect(screen.queryByText('检索片段 2')).not.toBeInTheDocument();
    expect(screen.getByText('精确匹配检索上下文原文')).toBeInTheDocument();
    expect(container.querySelector('img, script')).toBeNull();
    expect((globalThis as Record<string, unknown>).traceExecuted).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('ReportView 本地隔离预览', () => {
  it('只显示可复制的本地路径，未提供远程下载入口', () => {
    render(<ReportView result={resultWith()} />);
    expect(screen.getByText(localHtmlPath)).toBeInTheDocument();
    expect(screen.getByText(localChartPath)).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('手动导入，未自动核验与当前结果对应')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('从 File 本地读取 HTML，移除可执行资源，保留内联样式和内嵌图片', async () => {
    const source = `<!doctype html><html><head><style>body { color: red; }</style><script>globalThis.reportExecuted = true</script><meta http-equiv="refresh" content="0;url=https://never-load.invalid"></head><body><h1>原报告标题</h1><img src="data:image/png;base64,AA=="><img src="https://never-load.invalid/img"><iframe src="https://never-load.invalid/frame"></iframe><a href="https://never-load.invalid/link">远程链接文字</a><form action="https://never-load.invalid/upload"><input onfocus="alert(1)"></form></body></html>`;
    const read = vi.fn().mockResolvedValue(source);
    render(<ReportView result={resultWith()} />);
    chooseReport(textFile('original-report.html', read));
    const frame = await screen.findByTitle('本地报告预览：original-report.html');
    expect(read).toHaveBeenCalledTimes(1);
    expect(screen.getByText('original-report.html')).toBeInTheDocument();
    expect(screen.getByText('手动导入，未自动核验与当前结果对应')).toBeInTheDocument();
    expect(frame).toHaveAttribute('sandbox', '');
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
    const html = frame.getAttribute('srcdoc')!;
    const preview = new DOMParser().parseFromString(html, 'text/html');
    const policy = preview.head.firstElementChild;
    expect(policy?.getAttribute('http-equiv')).toBe('Content-Security-Policy');
    expect(policy?.getAttribute('content')).toContain("default-src 'none'");
    expect(policy?.getAttribute('content')).toContain("script-src 'none'");
    expect(policy?.getAttribute('content')).toContain("connect-src 'none'");
    expect(policy?.getAttribute('content')).toContain("style-src 'unsafe-inline'");
    expect(policy?.getAttribute('content')).toContain('img-src data:');
    expect(preview.querySelector('script, iframe, meta[http-equiv="refresh"], [onfocus], [action], [href]')).toBeNull();
    expect(preview.querySelector('img[src^="https:"]')).toBeNull();
    expect(preview.querySelector('img[src^="data:image/"]')).not.toBeNull();
    expect(preview.querySelector('style')?.textContent).toContain('color: red');
    expect(preview.querySelector('h1')?.textContent).toBe('原报告标题');
    expect((globalThis as Record<string, unknown>).reportExecuted).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('新的结果对象会清理已经导入的旧报告', async () => {
    const first = resultWith();
    const { rerender } = render(<ReportView result={first} />);
    chooseReport(textFile('old.html', () => Promise.resolve('<h1>旧报告</h1>')));
    await screen.findByTitle('本地报告预览：old.html');
    rerender(<ReportView result={{ ...first }} />);
    expect(screen.queryByTitle('本地报告预览：old.html')).not.toBeInTheDocument();
    expect(screen.queryByText('old.html')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '导入报告以开始预览' })).toBeInTheDocument();
  });

  it('结果切换期间完成的旧文件读取不会重新插入旧报告', async () => {
    let finishRead!: (source: string) => void;
    const pending = new Promise<string>((resolve) => { finishRead = resolve; });
    const first = resultWith();
    const { rerender } = render(<ReportView result={first} />);
    chooseReport(textFile('pending.html', () => pending));
    screen.getAllByRole('button', { name: '正在读取…' }).forEach((button) => expect(button).toBeDisabled());
    rerender(<ReportView result={{ ...first }} />);
    await act(async () => { finishRead('<h1>过期报告</h1>'); await pending; });
    expect(screen.queryByTitle('本地报告预览：pending.html')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '导入报告以开始预览' })).toBeInTheDocument();
  });

  it('File.text 不可用时通过 FileReader 读取现有文件', async () => {
    render(<ReportView result={null} />);
    const file = new File(['<h1>FileReader 报告</h1>'], 'fallback.html', { type: 'text/html' });
    Object.defineProperty(file, 'text', { value: undefined });
    chooseReport(file);
    const frame = await screen.findByTitle('本地报告预览：fallback.html');
    expect(frame.getAttribute('srcdoc')).toContain('FileReader 报告');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('读取失败有明确反馈，导入按钮恢复可用', async () => {
    render(<ReportView result={resultWith()} />);
    chooseReport(textFile('unreadable.html', () => Promise.reject(new Error('本地文件读取失败'))));
    expect(await screen.findByRole('alert')).toHaveTextContent('本地文件读取失败');
    expect(screen.getByRole('button', { name: '导入 HTML 报告' })).toBeEnabled();
    expect(screen.queryByTitle(/本地报告预览/)).not.toBeInTheDocument();
  });

  it('复制失败保留可选中的路径并提示手动复制', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('Clipboard denied'));
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    render(<ReportView result={resultWith()} />);
    fireEvent.click(screen.getByRole('button', { name: '复制HTML 报告本地路径' }));
    expect(await screen.findByRole('status')).toHaveTextContent('复制失败，请选中路径手动复制。');
    expect(writeText).toHaveBeenCalledWith(localHtmlPath);
    expect(screen.getByText(localHtmlPath)).toHaveAttribute('tabindex', '0');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('清除预览后仍可再次导入同名文件', async () => {
    render(<ReportView result={resultWith()} />);
    const file = textFile('repeat.html', () => Promise.resolve('<h1>报告</h1>'));
    chooseReport(file);
    await screen.findByTitle('本地报告预览：repeat.html');
    fireEvent.click(screen.getByRole('button', { name: '清除预览' }));
    expect(screen.queryByTitle('本地报告预览：repeat.html')).not.toBeInTheDocument();
    chooseReport(file);
    expect(await screen.findByTitle('本地报告预览：repeat.html')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: '评测报告' })).getByText('repeat.html')).toBeInTheDocument();
  });
});
