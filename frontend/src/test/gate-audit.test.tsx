import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { GateAuditView } from '../features/GateAuditView';
import { parseLabHash } from '../labRoute';
import audit from '../fixtures/gate-audit.json';

const article = (name: RegExp) => screen.getByRole('article', { name });

describe('Gate audit view', () => {
  it('shows the four questions with the frozen-evidence numbers', () => {
    render(<GateAuditView/>);
    const recompute = article(/门禁结论能不能复算/);
    expect(within(recompute).getByText('可精确重现')).toBeInTheDocument();
    expect(within(recompute).getAllByText('0.7375')).toHaveLength(2);
    expect(within(recompute).getAllByText('FAIL')).toHaveLength(3);

    const judges = article(/换一个裁判/);
    expect(within(judges).getByText('3 次均为 FAIL')).toBeInTheDocument();
    expect(within(judges).getAllByRole('img').map((bar) => bar.getAttribute('aria-label'))).toEqual([
      '原判定 正向忠实度 73.8%，门禁 FAIL', '原裁判重判 正向忠实度 75.8%，门禁 FAIL', 'Qwen2.5-72B 正向忠实度 72.3%，门禁 FAIL']);
    expect(within(judges).getByText('约 5.7 倍')).toBeInTheDocument();

    const sensitivity = article(/证据变了/);
    expect(within(sensitivity).getByText('59 / 60 符合预期')).toBeInTheDocument();
    const removal = within(sensitivity).getByRole('rowheader', { name: '移除支持片段' }).closest('tr')!;
    expect([...removal.querySelectorAll('td')].map((cell) => cell.textContent)).toEqual(['依据不足', '6 / 6', '5 / 5另 1 条解析失败']);

    const certainty = article(/这个 FAIL 有多确定/);
    expect(within(certainty).getByRole('img', { name: /正向忠实度 点估计 73\.8%，95% 区间 55\.0%–90\.0%/ })).toBeInTheDocument();
    expect(within(certainty).getByText('证据不足')).toBeInTheDocument();
    expect(within(certainty).getByText('确定通过')).toBeInTheDocument();
  });

  it('shows the gate self-check from the tests and the mutation archive', () => {
    render(<GateAuditView/>);
    const selfCheck = screen.getByRole('region', { name: '门禁代码本身对不对' });
    expect(within(selfCheck).getByRole('list', { name: '门禁性质' }).children).toHaveLength(10);
    expect(within(selfCheck).getByText('39 / 42')).toBeInTheDocument();
    const mutants = within(selfCheck).getByRole('list', { name: '42 个变异：抓住 39 个，存活 3 个，超时 0 个' });
    expect(mutants.querySelectorAll('.audit-mutant--survived')).toHaveLength(3);
    const survivors = within(selfCheck).getByRole('table', { name: '存活的 3 个变异都不影响裁决' });
    expect(within(survivors).getAllByRole('rowheader').map((cell) => cell.firstChild?.textContent)).toEqual(['#3', '#5', '#15']);
    expect(within(survivors).getByText('warning_flag = None')).toBeInTheDocument();
  });

  it('shows the regression example as a warning, not a block', () => {
    render(<GateAuditView/>);
    const regression = screen.getByRole('region', { name: '回归对比样例：这次比上次差了吗' });
    expect(within(regression).getByText('无确定退步')).toBeInTheDocument();
    const rows = within(regression).getAllByRole('row').slice(1);
    expect(rows.map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([
      ['正向忠实度', '0.8333', '0.6000', '3', '−0.0333', '[−0.1000, 0.0000]', '可能退步'],
      ['正向上下文精度', '0.8542', '0.9792', '4', '+0.1250', '[0.0000, +0.3750]', '未退步'],
    ]);
  });

  it('links every source to the public repository and is reachable by deep link', () => {
    render(<GateAuditView/>);
    const links = screen.getAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) expect(link.getAttribute('href')).toMatch(/^https:\/\/github\.com\/4444wjt\/RAG_Evaluation_Demo\/blob\/master\/[\w./-]+$/);
    expect(parseLabHash('#/lab/audit')).toEqual({ target: 'lab', view: 'audit' });
    expect(parseLabHash('#/lab/demo/audit')).toEqual({ target: 'demo', view: 'audit' });
  });

  it('is generated data, never a hand-edited fixture', () => {
    expect(audit.schemaVersion).toBe(1);
    expect(audit.mutation.targetSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(audit.mutation.killed + audit.mutation.survived + audit.mutation.timeouts).toBe(audit.mutation.total);
  });
});
