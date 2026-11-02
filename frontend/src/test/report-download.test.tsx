import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ReportView } from '../features/ReportView';
import demo from '../fixtures/demo.json';

vi.mock('../config', async original => ({ ...await original<typeof import('../config')>(), BYOK: true, READ_ONLY: false, STATIC_SITE: false }));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('cloud report delivery', () => {
  it('downloads the current isolated preview as HTML and clears the action with the preview', async () => {
    const record = { ...demo.result, record_id: 'a'.repeat(32), html_path: '/srv/private/report.html', chart_path: '/srv/private/chart.png' };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<h1>保存的报告</h1><script>alert(1)</script>')));
    let downloaded: Blob | undefined;
    const create = vi.fn((blob: Blob) => { downloaded = blob; return 'blob:download'; });
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function(this: HTMLAnchorElement) { expect(this.download).toBe('veriscope-report-' + record.record_id + '.html'); });
    render(<ReportView result={record}/>);
    const frame = await screen.findByTitle('历史报告预览');
    fireEvent.click(screen.getByRole('button', { name: '下载当前预览 HTML 报告' }));
    expect(click).toHaveBeenCalledTimes(1);
    expect(downloaded?.type).toBe('text/html;charset=utf-8');
    const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(downloaded!); });
    expect(text).toBe(frame.getAttribute('srcdoc'));
    expect(text).toContain('保存的报告');
    expect(text).not.toContain('<script');
    fireEvent.click(screen.getByRole('button', { name: '清除预览' }));
    expect(screen.queryByRole('button', { name: '下载当前预览 HTML 报告' })).not.toBeInTheDocument();
  });

  it('hides server paths in cloud mode and does not offer downloads without a report', () => {
    render(<ReportView result={{ ...demo.result, html_path: '/srv/private/report.html', chart_path: '/srv/private/chart.png' }}/>);
    expect(screen.queryByText('/srv/private/report.html')).not.toBeInTheDocument();
    expect(screen.queryByText('/srv/private/chart.png')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /复制.*本地路径/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下载当前预览 HTML 报告' })).not.toBeInTheDocument();
  });
});
