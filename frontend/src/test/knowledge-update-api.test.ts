import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportKU, fetchKUSnapshot, fetchKUSources, fetchKUVersions, lineCitation, logicalLines, makeKUInput, runKU } from '../api/knowledgeUpdate';
import type { KUItemInput, KUSourceInput } from '../api/knowledgeUpdate';

const source = (): KUSourceInput => ({
  source: { source_id: 'law', title: '法规', url: 'https://example.org', collector_ready: true, extraction: { fetch_backend: 'test', include_filters: [] } },
  old: { version: '123', text: '旧\r\n文本\n' }, new: { version: 'manual', text: '新文本' },
});
const item = (id: string, enabled: boolean): KUItemInput => ({
  key: 1, item_id: id, question: '问题', old_draft: '旧草稿', source_ids: ['law'], old_citations: [],
  revision: { enabled, proposed_draft: '新草稿', citations: [], open_questions: '待核查\r\n  \r\n另一项\n' },
});

afterEach(() => vi.unstubAllGlobals());

describe('输入与引用', () => {
  it.each(['con', 'CON', 'Aux', 'com1', 'lpt9', 'nul.txt', 'COM¹', 'COM².txt', 'LPT³', 'CON .txt', 'end.', 'end ', 'a?', 'a*', 'a"', 'a<', 'a>', 'a|', 'a/b', 'a\\b', 'a:b', 'a\u0000b', 'a\u001fb', 'a\u007fb', 'a\u009fb', '源'.repeat(86), '', '.', '..'])('拒绝不能生成路径的来源 ID %s', (id) => {
    const row = source(); row.source.source_id = id;
    expect(() => makeKUInput('t', [row], [item('a', false)], 'scan')).toThrow(/来源「法规」.*ID：/);
  });

  it.each(['com0', 'com10', 'console', 'a.b', 'nodejs-building', 'curl-install', ' con', 'a\u00a0', 'a\u2028b', '源'.repeat(85), 'long~123', 'x'.repeat(250)])('前端放行来源 ID 并保留原值 %s', (id) => {
    const row = source(); row.source.source_id = id;
    const result = makeKUInput('t', [row], [item('a', false)], 'scan');
    expect(JSON.parse(result.task).sources[0].source_id).toBe(id);
    expect(result.files[`sources/${id}/old.txt`]).toBe(row.old.text);
  });

  it('仅对实际生成的同侧路径检查 ASCII 大小写冲突', () => {
    const first = source(), second = source();
    first.source.source_id = 'Case'; second.source.source_id = 'case';
    expect(() => makeKUInput('t', [first, second], [item('a', false)], 'scan')).toThrow(/大小写冲突/);
    first.new = { version: 'none', text: null }; second.old = { version: 'none', text: null };
    expect(Object.keys(makeKUInput('t', [first, second], [item('a', false)], 'scan').files)).toEqual(['sources/Case/old.txt', 'sources/case/new.txt']);
    first.source.source_id = 'straße'; second.source.source_id = 'STRASSE';
    first.new = source().new; second.old = source().old;
    expect(() => makeKUInput('t', [first, second], [item('a', false)], 'scan')).not.toThrow();
  });

  it('保留原始文本，生成绑定路径、版本与修订 schema，未修订条目仍在任务中', () => {
    const input = makeKUInput('task-1', [source()], [item('a', true), item('b', false)], 'recheck');
    expect(input.files).toEqual({ 'sources/law/old.txt': '旧\r\n文本\n', 'sources/law/new.txt': '新文本' });
    expect(JSON.parse(input.task)).toEqual({ schema_version: 'knowledge-update-task/v1', task_id: 'task-1', sources: [{ source_id: 'law', old: { path: 'sources/law/old.txt', version_tag: '123' }, new: { path: 'sources/law/new.txt', version_tag: 'manual' } }], items: [expect.objectContaining({ item_id: 'a' }), expect.objectContaining({ item_id: 'b' })] });
    expect(JSON.parse(input.revisions!)).toEqual({ schema_version: 'knowledge-update-revisions/v1', revisions: [{ item_id: 'a', proposed_draft: '新草稿', citations: [], open_questions: ['待核查', '另一项'] }] });
    expect(makeKUInput('task-1', [source()], [item('a', true)], 'scan')).not.toHaveProperty('revisions');
  });

  it('明确缺失可以使用，但两边都无或加载失败不能当作删除', () => {
    const row = source();
    row.old = { version: 'none', text: null };
    expect(JSON.parse(makeKUInput('t', [row], [item('a', false)], 'scan').task).sources[0].old).toBeNull();
    row.new = { version: 'none', text: null };
    expect(() => makeKUInput('t', [row], [item('a', false)], 'scan')).toThrow(/不能同时为无/);
    row.new = { version: '123', text: null };
    expect(() => makeKUInput('t', [row], [item('a', false)], 'scan')).toThrow(/尚未就绪/);
  });

  it('逻辑行处理 CRLF、末尾分隔符、空白、重复行及 Python 其他分隔符', () => {
    expect(logicalLines('')).toEqual([]);
    expect(logicalLines('同\r\n  \r\n同\n\n')).toEqual(['同', '  ', '同', '']);
    expect(logicalLines('a\vb\fc\x1cd\x1de\x1ef\u0085g\u2028h\u2029')).toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']);
  });

  it('引用完整行并将反向选择规范为连续范围', () => {
    expect(lineCitation('law', '同\r\n  \r\n同\n', 3, 1)).toEqual({ source_id: 'law', quote: '同\n  \n同', line_start: 1, line_end: 3 });
    expect(lineCitation('law', '一\n二', 2).quote).toBe('二');
    expect(logicalLines(lineCitation('law', 'A\n\nB', 1, 2).quote)).toEqual(['A', '']);
    expect(logicalLines(lineCitation('law', 'A\n\nB', 2).quote)).toEqual(['']);
    expect(() => lineCitation('law', '一', 0)).toThrow();
    expect(() => lineCitation('law', '一', 1, 2)).toThrow();
  });
});

describe('请求', () => {
  it('加载清单时给出来源名称、原 ID 和原因', async () => {
    const bad = { ...source().source, source_id: 'con', title: '测试来源' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sources: [bad] }))));
    await expect(fetchKUSources()).rejects.toThrow(/测试来源.*con.*保留名称/);
  });

  it('加载合法和仅大小写不同的来源清单', async () => {
    const list = ['Case', 'case', '源'.repeat(85)].map((id) => ({ ...source().source, source_id: id }));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sources: list }))));
    await expect(fetchKUSources()).resolves.toEqual(list);
  });
  it('读取 JSON 响应体时外部取消传递到 fetch signal 并返回 CANCELLED', async () => {
    const controller = new AbortController();
    const response = new Response('{}');
    let fetchSignal!: AbortSignal;
    let bodyStarted!: () => void;
    const started = new Promise<void>((resolve) => { bodyStarted = resolve; });
    vi.spyOn(response, 'json').mockImplementation(() => new Promise((_resolve, reject) => {
      fetchSignal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      bodyStarted();
    }));
    vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => {
      fetchSignal = init.signal as AbortSignal;
      return Promise.resolve(response);
    }));
    const pending = fetchKUSources(controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ code: 'CANCELLED' });
    await started;
    expect(fetchSignal.aborted).toBe(false);
    controller.abort();
    expect(fetchSignal.aborted).toBe(true);
    await rejected;
  });

  it('ZIP 响应体读取超过 30 秒时中止 fetch signal 并返回 TIMEOUT', async () => {
    vi.useFakeTimers();
    try {
      const response = new Response('zip', { headers: { 'Content-Type': 'application/zip' } });
      let fetchSignal!: AbortSignal;
      let bodyStarted!: () => void;
      const started = new Promise<void>((resolve) => { bodyStarted = resolve; });
      vi.spyOn(response, 'blob').mockImplementation(() => new Promise((_resolve, reject) => {
        fetchSignal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
        bodyStarted();
      }));
      vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => {
        fetchSignal = init.signal as AbortSignal;
        return Promise.resolve(response);
      }));
      const pending = exportKU('scan', makeKUInput('t', [source()], [item('a', false)], 'scan'));
      const rejected = expect(pending).rejects.toMatchObject({ code: 'TIMEOUT' });
      await started;
      expect(fetchSignal.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(30_000);
      expect(fetchSignal.aborted).toBe(true);
      await rejected;
    } finally {
      vi.useRealTimers();
    }
  });

  it('版本 URL 编码来源，快照只接受明确数字时间戳', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ source_id: 'a/b', versions: [{ timestamp: '123', captured_at: 'today' }] }))).mockResolvedValueOnce(new Response(JSON.stringify({ source_id: 'a/b', timestamp: '123', captured_at: 'today', sha256: 'x', size: 1, text: 'x' })));
    vi.stubGlobal('fetch', fetchMock);
    await fetchKUVersions('a/b');
    await fetchKUSnapshot('a/b', '123');
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(['/api/knowledge-update/sources/a%2Fb/versions', '/api/knowledge-update/sources/a%2Fb/versions/123']);
    await expect(fetchKUSnapshot('a/b', 'latest')).rejects.toThrow(/数值时间戳/);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([[503, '手动粘贴'], [502, '不表示来源已删除'], [404, '不存在'], [413, '8 MiB'], [422, '完整诊断']] as const)('HTTP %i 保留诊断并显示可操作提示', async (status, message) => {
    const diagnostic = { detail: { code: 'TEST_ERROR', message: 'detail' } };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(diagnostic), { status })));
    await expect(fetchKUSources()).rejects.toMatchObject({ status, code: 'TEST_ERROR', diagnostic, message: expect.stringContaining(message) });
  });

  it('scan 和 export 提交原始输入，export 带 mode 且只接受 ZIP', async () => {
    const input = makeKUInput('t', [source()], [item('a', false)], 'scan');
    const bundle = { report: { mode: 'scan', run_status: 'COMPLETED', summary: {}, bindings: {}, sources: [], items: [] }, run: {}, handover: 'text' };
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(bundle))).mockResolvedValueOnce(new Response('zip data', { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': 'attachment; filename="knowledge-update-scan-abcdef123456.zip"' } })).mockResolvedValueOnce(new Response('not zip', { headers: { 'Content-Type': 'text/html' } }));
    vi.stubGlobal('fetch', fetchMock);
    await runKU('scan', input);
    const result = await exportKU('scan', input);
    expect(result.filename).toBe('knowledge-update-scan-abcdef123456.zip');
    expect(result.blob.type).toBe('application/zip');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(input);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ ...input, mode: 'scan' });
    await expect(exportKU('scan', input)).rejects.toThrow(/未返回 ZIP/);
  });
});
