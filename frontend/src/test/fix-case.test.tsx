import { createHash, webcrypto } from 'node:crypto';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { FixCaseTour } from '../features/FixCaseTour';
import { CONTRAST, HASH_NOTE, MAIN, REPLAY_TEXT, REVIEW_CARD_FILENAME, SOURCE_FILES, reviewCard } from '../features/fixCase';
import fixData from '../fixtures/fix-verification.json';
import caseData from '../fixtures/fix-case.json';
import { LandingPage } from '../features/landing/LandingPage';

const tour = () => screen.getByRole('region', { name: '一次发布拦截，怎样复核' });
const tab = (name: RegExp) => within(tour()).getByRole('tab', { name });

const mockDownload = () => {
  const createObjectURL = vi.fn((_blob: Blob) => 'blob:review-card');
  const revokeObjectURL = vi.fn();
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }));
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  return { createObjectURL, revokeObjectURL, click };
};

const readBlob = (blob: Blob) => new Promise<ArrayBuffer>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(reader.result as ArrayBuffer);
  reader.onerror = () => reject(reader.error);
  reader.readAsArrayBuffer(blob);
});

const travel = async (direction: 'back' | 'forward') => {
  await act(async () => {
    const changed = new Promise<void>((resolve) => window.addEventListener('popstate', () => resolve(), { once: true }));
    window.history[direction]();
    await changed;
  });
};

describe('Fix case tour', () => {
  beforeEach(() => window.history.replaceState({}, '', '#/lab/fix'));

  it('opens on the block and names its own runs, not the workspace result', () => {
    render(<FixCaseTour/>);
    expect(tab(/看拦截/)).toHaveAttribute('aria-selected', 'true');
    const panel = within(tour()).getByRole('tabpanel');
    expect(within(panel).getByText('0.7375')).toBeInTheDocument();
    expect(within(panel).getByText('门禁 FAIL')).toBeInTheDocument();
    expect(within(panel).getByText('20261005T144504Z-full-39511424')).toBeInTheDocument();
    expect(within(tour()).getByText(/与侧栏「当前评测」无关/)).toBeInTheDocument();
  });

  it('shows the original refusal, an empty trace as no trace, and the retrieved text verbatim', () => {
    render(<FixCaseTour/>);
    fireEvent.click(tab(/查原文/));
    const panel = within(tour()).getByRole('tabpanel');
    expect(within(panel).getByText(MAIN.question)).toBeInTheDocument();
    expect(within(panel).getByText('原记录无逐句判定')).toBeInTheDocument();
    expect(within(panel).queryByText(/不支持 ·|fabrication/)).not.toBeInTheDocument();
    const marks = [...panel.querySelectorAll('mark')].map((node) => node.textContent);
    expect(marks).toEqual(['投保机构应当按照存款保险基金管理机构的规定，每6个月交纳一次保费', '投保机构向存款保险基金管理机构交纳保费']);
    const passages = [...panel.querySelectorAll('.fx-passage-text')].map((node) => node.textContent);
    expect(passages).toEqual(MAIN.contexts);
  });

  it('keeps F04 beside F03 and moves with the keyboard', () => {
    render(<FixCaseTour/>);
    fireEvent.keyDown(tab(/看拦截/), { key: 'End' });
    expect(tab(/可复算/)).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tab(/可复算/), { key: 'ArrowLeft' });
    const panel = within(tour()).getByRole('tabpanel');
    expect(within(panel).getAllByText(/supported · 有依据/)).toHaveLength(4);
    expect(within(panel).getByLabelText(`${CONTRAST.id} 对照`)).toHaveTextContent('仍拒答');
    expect(within(panel).getByText(/trace_status：empty_answer/)).toBeInTheDocument();
    expect(window.history.state.fixStep).toBe(2);
  });

  it('keeps the uncertainty and the judge rerun next to the PASS', () => {
    render(<FixCaseTour/>);
    fireEvent.click(tab(/可复算/));
    const panel = within(tour()).getByRole('tabpanel');
    expect(within(panel).getByText('0.8607')).toBeInTheDocument();
    expect(within(panel).getByText(/0.7286–0.9667 跨过 0.8/)).toBeInTheDocument();
    expect(within(panel).getByText(/0.7992，门禁 FAIL/)).toBeInTheDocument();
    for (const link of within(panel).getAllByRole('link')) expect(link.getAttribute('href')).toContain(caseData.pinned.commit);
  });

  it('reads the current history entry on a new home-page entry and on reload, Back and Forward', async () => {
    window.history.replaceState({ labView: 'fix', workspaceMarker: 'original' }, '', '#/lab/fix');
    const mounted = render(<FixCaseTour/>);
    fireEvent.click(tab(/看处理/));
    expect(window.history.state).toEqual({ labView: 'fix', workspaceMarker: 'original', fixStep: 2 });

    act(() => {
      window.history.pushState({}, '', '#/');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
      window.history.pushState({ labView: 'fix', workspaceMarker: 'kept' }, '', '#/lab/fix');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(tab(/看拦截/)).toHaveAttribute('aria-selected', 'true');
    expect(window.history.state).toEqual({ labView: 'fix', workspaceMarker: 'kept' });
    mounted.unmount();
    render(<FixCaseTour/>);
    expect(tab(/看拦截/)).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(tab(/查原文/));
    expect(window.history.state).toEqual({ labView: 'fix', workspaceMarker: 'kept', fixStep: 1 });

    await travel('back');
    expect(window.location.hash).toBe('#/');
    await travel('back');
    expect(tab(/看处理/)).toHaveAttribute('aria-selected', 'true');
    expect(window.history.state.workspaceMarker).toBe('original');
    await travel('forward');
    await travel('forward');
    expect(tab(/查原文/)).toHaveAttribute('aria-selected', 'true');
    expect(window.history.state.workspaceMarker).toBe('kept');
  });

  it('states what source hashes bind and shows commands for the exact files', () => {
    render(<FixCaseTour/>);
    fireEvent.click(tab(/可复算/));
    const panel = within(tour()).getByRole('tabpanel');
    expect(within(panel).getByText(new RegExp(HASH_NOTE))).toBeInTheDocument();
    for (const file of SOURCE_FILES) {
      expect(within(panel).getByText(`sha256sum ${file.path}`)).toBeInTheDocument();
      expect(within(panel).getByLabelText(`归档检出 SHA-256 ${file.sha256}`)).toBeInTheDocument();
      if (file.sha256 !== file.gitBlobSha256) expect(within(panel).getByLabelText(`链接原始文件 SHA-256 ${file.gitBlobSha256}`)).toBeInTheDocument();
    }
    const card = reviewCard();
    expect(card).toContain(HASH_NOTE);
    expect(card).toContain(`sha256sum ${REVIEW_CARD_FILENAME}`);
    for (const file of SOURCE_FILES) expect(card).toContain(`sha256sum ${file.path}`);
  });

  it('keeps commands selectable when writing plain text to the clipboard is refused', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('permission denied'));
    Object.assign(navigator, { clipboard: { writeText } });
    render(<FixCaseTour/>);
    fireEvent.click(within(tour()).getByRole('button', { name: '复制回放命令' }));
    expect(writeText).toHaveBeenCalledWith(REPLAY_TEXT);
    await waitFor(() => expect(within(tour()).getByRole('status')).toHaveTextContent('复制失败：浏览器未允许写入剪贴板。命令在第 ④ 步，可选中后手动复制。'));
    fireEvent.click(tab(/可复算/));
    expect(tour().querySelector('.fx-tour-commands')?.textContent).toBe(`${REPLAY_TEXT}\n`);
  });

  it('declares the SHA-256 of the downloaded UTF-8 bytes and repeats those exact bytes', async () => {
    vi.stubGlobal('crypto', webcrypto);
    const { createObjectURL, revokeObjectURL, click } = mockDownload();
    render(<FixCaseTour/>);
    const digest = within(tour()).getByLabelText('复核卡文件 SHA-256');
    await waitFor(() => expect(digest).toHaveTextContent(/^[0-9a-f]{64}$/));
    const save = within(tour()).getByRole('button', { name: '下载复核卡' });
    fireEvent.click(save);
    fireEvent.click(save);
    expect(createObjectURL).toHaveBeenCalledTimes(2);
    expect(click).toHaveBeenCalledTimes(2);
    for (const [blob] of createObjectURL.mock.calls) {
      expect(blob.type).toBe('text/markdown;charset=utf-8');
      const bytes = await readBlob(blob);
      const content = new TextDecoder().decode(bytes);
      expect(content).toBe(reviewCard());
      expect(createHash('sha256').update(new Uint8Array(bytes)).digest('hex')).toBe(digest.textContent);
      expect(content).not.toContain(digest.textContent!);
    }
    expect(within(tour()).getByRole('status')).toHaveTextContent(`已发起浏览器下载 ${REVIEW_CARD_FILENAME}`);
    expect(within(tour()).getByRole('status')).toHaveTextContent('无法检测取消或系统权限问题');
    expect(document.querySelector('a[download]')).toBeNull();
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledTimes(2));
  });

  it.each(['unsupported', 'rejected'])('shows an unavailable digest when SHA-256 is %s', async (mode) => {
    vi.stubGlobal('crypto', mode === 'unsupported' ? {} : { subtle: { digest: vi.fn().mockRejectedValue(new Error('digest failed')) } });
    render(<FixCaseTour/>);
    await waitFor(() => expect(within(tour()).getByLabelText('复核卡文件 SHA-256')).toHaveTextContent('未取得 SHA-256（浏览器不支持或计算失败）'));
    expect(within(tour()).getByLabelText('复核卡文件 SHA-256')).not.toHaveTextContent(/[0-9a-f]{64}/);
  });

  it.each(['Blob', 'URL', 'click'])('reports a download preparation failure when %s throws', async (stage) => {
    const { createObjectURL, revokeObjectURL, click } = mockDownload();
    if (stage === 'Blob') vi.stubGlobal('Blob', class { constructor() { throw new Error('Blob unavailable'); } });
    if (stage === 'URL') createObjectURL.mockImplementation(() => { throw new Error('object URL failed'); });
    if (stage === 'click') click.mockImplementation(() => { throw new Error('download blocked'); });
    render(<FixCaseTour/>);
    fireEvent.click(within(tour()).getByRole('button', { name: '下载复核卡' }));
    expect(within(tour()).getByRole('status')).toHaveTextContent('下载准备失败，未能发起浏览器下载');
    expect(within(tour()).getByRole('status')).not.toHaveTextContent('已发起浏览器下载');
    expect(document.querySelector('a[download]')).toBeNull();
    if (stage === 'click') await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledOnce());
    else expect(click).not.toHaveBeenCalled();
  });

  it('builds the review card from the same data and copies the same commands', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<FixCaseTour/>);
    fireEvent.click(within(tour()).getByRole('button', { name: '复制回放命令' }));
    expect(writeText).toHaveBeenCalledWith(REPLAY_TEXT);
    expect(await within(tour()).findByText('已复制 4 条回放命令。')).toBeInTheDocument();
    const card = reviewCard();
    expect(card).toContain('历史案例展示，未执行新的评测');
    for (const value of [fixData.formal.gate.before.posFaithfulness, fixData.formal.gate.after.posFaithfulness, fixData.judgeRerun.gate.posFaithfulness]) expect(card).toContain(String(value));
    for (const digest of [...Object.values(caseData.sources.baseline.sha256), ...Object.values(caseData.sources.fixed.sha256)]) expect(card).toContain(digest);
    expect(card).toContain(MAIN.after.answer);
    expect(card).toContain('Trace 不参与门禁计算');
    expect(card).toContain('基线 Trace 未记录返回身份');
    expect(card).toContain(caseData.sources.baseline.gitBlobSha256['analysis.json']);
    expect(card).toContain('归档检出 SHA-256');
    expect(card).toContain('固定链接原始文件 SHA-256');
    expect(card).toContain(REPLAY_TEXT);
  });
});

describe('Landing entry to the case', () => {
  it('makes the case the primary action and keeps the demo as a secondary entry', () => {
    const onEnter = vi.fn();
    render(<LandingPage onEnter={onEnter}/>);
    const primary = screen.getByRole('link', { name: '看一次拦截如何复核' });
    expect(primary).toHaveAttribute('href', '#/lab/fix');
    expect(primary).toHaveClass('btn-primary');
    fireEvent.click(primary);
    expect(onEnter).toHaveBeenLastCalledWith('lab', 'fix');
    expect(screen.getByText('看到发布拦截的依据，回查回答与原文，核验处理后的结果。')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: '查看演示' })[0]).toHaveAttribute('href', '#/lab/demo');
  });
});
