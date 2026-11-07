import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AnswerImprovementView } from '../features/AnswerImprovementView';
import { parseLabHash } from '../labRoute';
import type { EvaluationResult } from '../api/types';
import { createImprovement, downloadImprovement } from '../api/answerImprovement';
import { createHash, webcrypto } from 'node:crypto';

const result: EvaluationResult = { summary: [], summary_df: [], gate_passed: false, gate_message: '原始失败', chart_path: null, html_path: null, total_elapsed_seconds: 1, record_id: 'a'.repeat(32), model_dfs: { first: [{ question: '期限？', answer: '期限为14天。', contexts: ['期限为21天。'] }], second: [{ question: '第二题', answer: '其他回答', contexts: [] }] } };
const candidate = () => ({ schema_version: 1, id: 'b'.repeat(32), created_at: '2026-11-02T00:00:00Z', parent_record_id: result.record_id, parent_result_sha256: '0'.repeat(64), model_name: 'first', scope: 'single-quantity-literal-v1', original_gate: { passed: false, message: '原始失败', faith_threshold: 0.8, ctx_prec_threshold: 0.8 }, bindings: { input_sha256: '1'.repeat(64), generator_version: 'v1', verifier_version: 'v1', record_sha256: '2'.repeat(64) }, items: [{ row_index: 0, question: '期限？', original_answer: '期限为14天。', contexts: ['期限为21天。'], status: 'verified_candidate', candidate_answer: '期限为21天。', edits: [{ start: 3, end: 5, before: '14', after: '21', context_index: 0, evidence: '期限为21天。' }], verification: { status: 'verified', checks: [{ code: 'patch_integrity', passed: true }], semantic_evaluation: 'NOT_EVALUATED' }, unresolved_reasons: ['仍需人工确认语义'] }], counts: { total_rows: 1, changed_rows: 1, verified_rows: 1, no_candidate_rows: 0, review_rows: 0 }, rescoring: { status: 'not_run', result: null } });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
let requests: ReturnType<typeof vi.fn>;
beforeEach(() => { requests = vi.fn().mockResolvedValue(json(candidate())); vi.stubGlobal('fetch', requests); });

describe('数字局部候选', () => {
  it('支持现有工作台深链', () => { expect(parseLabHash('#/lab/answer-improvement')).toEqual({ target: 'lab', view: 'answer-improvement' }); });
  it('演示和缺失已保存记录都不生成候选', () => {
    const page = render(<AnswerImprovementView result={result} source="demo"/>);
    expect(screen.getByRole('button', { name: '生成数字局部候选' })).toBeDisabled();
    page.rerender(<AnswerImprovementView result={{ ...result, record_id: null }} source="history"/>);
    expect(screen.getByText(/保存记录或通过历史记录导入/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成数字局部候选' })).toBeDisabled();
    expect(requests).not.toHaveBeenCalled();
  });
  it('保留原FAIL，显示逐题补丁、来源、未解决与未重新评分', async () => {
    render(<AnswerImprovementView result={result} source="history" initialModel="first"/>);
    await userEvent.click(screen.getByRole('button', { name: '生成数字局部候选' }));
    expect(await screen.findByText('期限为21天。', { selector: '.ai-answer' })).toBeInTheDocument();
    expect(screen.getByText('期限为14天。', { selector: '.ai-answer' })).toBeInTheDocument();
    expect(screen.getByText('仍需人工确认语义')).toBeInTheDocument();
    expect(screen.getByText('原始门禁 FAIL')).toBeInTheDocument();
    expect(screen.getByText(/新门禁与 Ragas：未重新评分/)).toBeInTheDocument();
    expect(screen.getByText('字面与补丁复核一致')).toBeInTheDocument();
    expect(requests).toHaveBeenCalledWith(`/api/evaluations/${result.record_id}/improvements`, expect.objectContaining({ method: 'POST', body: JSON.stringify({ model_name: 'first' }) }));
    expect(result.model_dfs.first[0].answer).toBe('期限为14天。');
  });
  it('切换模型清除候选，迟到响应不串题', async () => {
    let resolve!: (response: Response) => void;
    requests.mockImplementation(() => new Promise<Response>((done) => { resolve = done; }));
    render(<AnswerImprovementView result={result} source="history"/>);
    await userEvent.click(screen.getByRole('button', { name: '生成数字局部候选' }));
    fireEvent.change(screen.getByLabelText('候选模型'), { target: { value: 'second' } });
    await act(async () => { resolve(json(candidate())); });
    expect(screen.queryByText('字面与补丁复核一致')).not.toBeInTheDocument();
    expect(screen.getByLabelText('候选模型')).toHaveValue('second');
  });
  it('重核失败明确保留人工复核状态', async () => {
    requests.mockResolvedValueOnce(json(candidate())).mockResolvedValueOnce(json({ schema_version: 1, improvement_id: 'b'.repeat(32), status: 'failed', checks: [{ code: 'binding', passed: false }], items: [], semantic_evaluation: 'NOT_EVALUATED', rescoring: { status: 'not_run', result: null } }));
    render(<AnswerImprovementView result={result} source="history"/>);
    await userEvent.click(screen.getByRole('button', { name: '生成数字局部候选' }));
    await userEvent.click(await screen.findByRole('button', { name: '重新核验字面与补丁' }));
    await waitFor(() => expect(screen.getByText('重新核验：待人工复核')).toBeInTheDocument());
    expect(screen.queryByText('字面与补丁复核一致')).not.toBeInTheDocument();
    expect(screen.getByText(/本次重核未一致/)).toBeInTheDocument();
    expect(screen.getByText(/新门禁与 Ragas：未重新评分/)).toBeInTheDocument();
  });
  it('服务拒绝时保留原题并展示错误', async () => {
    requests.mockResolvedValue(json({ detail: '记录不存在' }, 404));
    render(<AnswerImprovementView result={result} source="history"/>);
    await userEvent.click(screen.getByRole('button', { name: '生成数字局部候选' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('记录不存在');
    expect(screen.getByText('原始门禁 FAIL')).toBeInTheDocument();
  });
  it('连续复核成功、请求失败、恢复时撤下旧成功且保留候选与原门禁', async () => {
    const verified = { schema_version: 1, improvement_id: 'b'.repeat(32), status: 'verified', checks: [{ code: 'binding', passed: true }], items: [], semantic_evaluation: 'NOT_EVALUATED', rescoring: { status: 'not_run', result: null } };
    requests.mockResolvedValueOnce(json(candidate())).mockResolvedValueOnce(json(verified))
      .mockResolvedValueOnce(json({ detail: '本次复核服务异常' }, 500)).mockResolvedValueOnce(json(verified));
    render(<AnswerImprovementView result={result} source="history"/>);
    await userEvent.click(screen.getByRole('button', { name: '生成数字局部候选' }));
    const recheck = await screen.findByRole('button', { name: '重新核验字面与补丁' });
    await userEvent.click(recheck);
    expect(await screen.findByText('重新核验：字面与补丁复核一致')).toBeInTheDocument();
    await userEvent.click(recheck);
    expect(await screen.findByRole('alert')).toHaveTextContent('本次复核服务异常');
    expect(screen.queryByText('重新核验：字面与补丁复核一致')).not.toBeInTheDocument();
    expect(screen.queryByText('字面与补丁复核一致')).not.toBeInTheDocument();
    expect(screen.getByText('本次复核未完成，待复核')).toBeInTheDocument();
    expect(screen.getByText('期限为21天。', { selector: '.ai-answer' })).toBeInTheDocument();
    expect(screen.getByText('原始门禁 FAIL')).toBeInTheDocument();
    expect(screen.getByText(/新门禁与 Ragas：未重新评分/)).toBeInTheDocument();
    await userEvent.click(recheck);
    expect(await screen.findByText('重新核验：字面与补丁复核一致')).toBeInTheDocument();
    expect(screen.queryByText('本次复核未完成，待复核')).not.toBeInTheDocument();
    expect(screen.getByText('字面与补丁复核一致')).toBeInTheDocument();
  });
  it('拒绝与当前模型不匹配或缺检查项的记录', async () => {
    requests.mockResolvedValueOnce(json({ ...candidate(), model_name: 'second' }));
    await expect(createImprovement(result.record_id!, 'first')).rejects.toThrow('不匹配');
    const malformed = candidate();
    malformed.items[0].verification.checks = [{ code: 'broken', passed: 'yes' as unknown as boolean }];
    requests.mockResolvedValueOnce(json(malformed));
    await expect(createImprovement(result.record_id!, 'first')).rejects.toThrow('不完整');
  });
  it('完整下载保留服务端字节，SHA绑定实际下载文件', async () => {
    const originalBytes = '\ufeff{ "candidate": "21天" }\r\n';
    const response = new Response(originalBytes, { headers: { 'Content-Type': 'application/json' } });
    const sourceBytes = await response.clone().arrayBuffer();
    requests.mockResolvedValueOnce(response);
    vi.stubGlobal('crypto', webcrypto);
    let downloaded: Blob | null = null;
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn((value: Blob) => { downloaded = value; return 'blob:candidate-test'; }) });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const binding = await downloadImprovement('b'.repeat(32));
    expect(click).toHaveBeenCalledOnce();
    expect(Buffer.from(await downloaded!.arrayBuffer())).toEqual(Buffer.from(sourceBytes));
    expect(binding).toEqual({ sha256: createHash('sha256').update(Buffer.from(sourceBytes)).digest('hex'), size: sourceBytes.byteLength });
  });
});
