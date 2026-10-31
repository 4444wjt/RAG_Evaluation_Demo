import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeUpdateView } from '../features/KnowledgeUpdateView';

const mode = vi.hoisted(() => ({ readOnly: false, staticSite: false }));
vi.mock('../config', async (original) => ({ ...await original<typeof import('../config')>(), get READ_ONLY() { return mode.readOnly; }, get STATIC_SITE() { return mode.staticSite; } }));

const NOTICE = '公开演示站不接入采集服务，不能抓取来源，请在本地部署后使用知识更新。';

beforeEach(() => {
  mode.readOnly = false; mode.staticSite = false;
  window.history.replaceState({}, '', '#/lab/knowledge-update');
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ sources: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
});
afterEach(() => { mode.readOnly = false; mode.staticSite = false; });

describe('knowledge update read-only notice', () => {
  it('shows the notice below the step rail and before the step body in a READ_ONLY build', async () => {
    mode.readOnly = true;
    const { container } = render(<KnowledgeUpdateView/>);
    const notice = container.querySelector('.readonly-notice')!;
    expect(notice).toHaveAttribute('role', 'status');
    expect(notice).toHaveTextContent(`只读演示${NOTICE}`);
    expect(container.querySelector('.ku-rail')!.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(notice.compareDocumentPosition(container.querySelector('.ku-step-body')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await screen.findByText('审核来源清单为空。');
  });

  it('does not show the notice in a normal build', async () => {
    const { container } = render(<KnowledgeUpdateView/>);
    await screen.findByText('审核来源清单为空。');
    expect(container.querySelector('.readonly-notice')).toBeNull();
  });
});
