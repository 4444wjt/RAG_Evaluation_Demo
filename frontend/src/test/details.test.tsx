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

function rowWith(trace: ResultRow['hallucination_trace']): ResultRow {
  return { question: '原始测试问题', answer: '原始模型回答', contexts: '原始上下文', hallucination_trace: trace };
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

  it.each(['', '{broken-json', JSON.stringify({ statements: [] })])('空或损坏 trace 使用中性状态：%s', (trace) => {
    render(<TraceView result={resultWith({ model: [rowWith(trace)] })} />);
    expect(screen.getByRole('heading', { name: '暂无逐句溯源' })).toBeInTheDocument();
    expect(screen.getByText('当前记录没有可读取的逐句判定，不能据此判断幻觉情况。')).toBeInTheDocument();
    expect(screen.queryByText(/零幻觉|无幻觉|0%|0 个幻觉/)).not.toBeInTheDocument();
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

  it('逐条展示原判定，仅为 supported 的精确证据标记真实上下文片段', () => {
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
    expect(screen.queryByText('精确匹配检索片段 1')).not.toBeInTheDocument();
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
