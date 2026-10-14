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

  it('attributes the formal-run FAIL to generation refusals on false-premise questions', () => {
    render(<GateAuditView/>);
    const panel = screen.getByRole('region', { name: '失败归因：该修检索还是生成' });
    expect(within(panel).getByText('主要在生成')).toBeInTheDocument();
    expect(within(panel).getByRole('img', { name: '正向忠实度 73.8%；生成拒答 拉低 20.0%，其余题未满分 拉低 6.3%；门槛 80.0%' })).toBeInTheDocument();
    const rows = within(panel).getAllByRole('row').slice(1);
    expect(rows.map((row) => [row.children[0].textContent, row.children[1].textContent, row.children[2].textContent])).toEqual([
      ['#21', '生成拒答', '100.0% · 0.0000'], ['#23', '生成拒答', '100.0% · 0.0000'],
      ['#24', '生成拒答', '100.0% · 0.0000'], ['#25', '生成拒答', '100.0% · 0.0000']]);
    expect(within(panel).getByText(/问题带错误前提/)).toBeInTheDocument();
  });

  it('shows the external benchmark with its limitation stated next to the result', () => {
    render(<GateAuditView/>);
    const panel = screen.getByRole('region', { name: '外部基准：逐句核对本身准不准' });
    expect(within(panel).getByText('幻觉召回 89.0%')).toBeInTheDocument();
    const confusion = within(panel).getByRole('table');
    expect(within(confusion).getAllByRole('row').slice(1).map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([
      ['无', '72', '2', '25', '1'], ['矛盾', '13', '49', '38', '0'], ['无法验证', '9', '6', '85', '0']]);
    expect(within(panel).getByText('答对 40 / 40；正确回答被判有幻觉 0 / 40')).toBeInTheDocument();
    expect(within(panel).getByText('只给出错误答案 11 题，逐句核对标出 3 题；其余照抄资料的判为有依据')).toBeInTheDocument();
    expect(within(panel).getByText('查不出资料错误')).toBeInTheDocument();
    expect(within(panel).getByText(/约为 0\.7178/)).toBeInTheDocument();
    expect(within(panel).getByText(/请求名 deepseek-chat，实际模型 DeepSeek-V4\.1-Flash（服务端返回名 deepseek-flash）/)).toBeInTheDocument();
  });

  it('links every source to the public repository and is reachable by deep link', () => {
    render(<GateAuditView/>);
    const links = screen.getAllByRole('link');
    expect(links.length).toBeGreaterThan(0);
    const inApp = links.filter((link) => link.getAttribute('href')?.startsWith('#'));
    expect(inApp.map((link) => link.getAttribute('href'))).toEqual(['#/lab/annotate']);
    for (const link of links.filter((link) => !inApp.includes(link))) expect(link.getAttribute('href')).toMatch(/^https:\/\/gitee\.com\/kkbushishou\/rag-hallucination-evaluator\/blob\/master\/[\w./-]+$/);
    expect(parseLabHash('#/lab/audit')).toEqual({ target: 'lab', view: 'audit' });
    expect(parseLabHash('#/lab/demo/audit')).toEqual({ target: 'demo', view: 'audit' });
  });

  it('is generated data, never a hand-edited fixture', () => {
    expect(audit.schemaVersion).toBe(1);
    expect(audit.mutation.targetSha256).toMatch(/^[0-9a-f]{64}$/);
    expect(audit.mutation.killed + audit.mutation.survived + audit.mutation.timeouts).toBe(audit.mutation.total);
  });
  it('separates the judge-error calibration into sensitivity, simulation and the archived human calibration', () => {
    render(<GateAuditView/>);
    const panel = screen.getByRole('region', { name: '裁判判错校准：把裁判误差也算进区间' });
    expect(within(panel).getByText(/已有 1 位非作者标完全部 52 条：人工口径跨过门槛，取决于 1 条未决陈述，按事先写定的口径不下结论，已记录的 FAIL 不改/)).toBeInTheDocument();
    expect(within(panel).getByText('单人标注 · 未下结论')).toBeInTheDocument();
    const states = within(panel).getAllByRole('region');
    expect(states.map((state) => [state.querySelector('h3')?.textContent, state.querySelector('.audit-chip')?.textContent])).toEqual([
      ['敏感性分析', '已完成'], ['模拟验证', '已完成'], ['真实人工校准', '已完成 · 1 人']]);

    const sensitivity = within(panel).getByRole('region', { name: '敏感性分析' });
    expect(within(sensitivity).getByText('5 条', { selector: 'dd' })).toBeInTheDocument();
    expect(within(within(sensitivity).getByRole('list', { name: '需要改判的陈述' })).getAllByRole('listitem')).toHaveLength(5);
    expect(within(sensitivity).getByText(/不估计裁判实际判错了多少/)).toBeInTheDocument();

    const simulation = within(panel).getByRole('region', { name: '模拟验证' });
    const rows = within(simulation).getAllByRole('row').map((row) => row.textContent);
    expect(rows).toContain(`校准校正·聚集${audit.calibration.simulation.coverage.invariant.leeClustered.map((v) => `${(v * 100).toFixed(1)}%`).join('–')}${audit.calibration.simulation.coverage.shifted.leeClustered.map((v) => `${(v * 100).toFixed(1)}%`).join('–')}`);
    expect(within(simulation).getByText(/不代表门禁裁判的真实误差/)).toBeInTheDocument();

    const human = within(panel).getByRole('region', { name: '真实人工校准' });
    expect(audit.calibration.annotation.status).toBe('done');
    expect(within(human).getByText(/没有用模型代替人工标注/)).toBeInTheDocument();
    // The bounds straddle the threshold because of one unresolved statement: no interval, no verdict.
    expect(within(human).getByText('77.5%–82.5%', { selector: 'dd' })).toBeInTheDocument();
    expect(within(human).getByText('F03 第 1 条')).toBeInTheDocument();
    expect(within(human).getByText(/有未决陈述时不给 95% 区间，也不下结论/)).toBeInTheDocument();
    expect(within(human).getByText(/D01 第 1 条、D09 第 1 条）都是裁判更严/)).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: '人工标注结果' })).toHaveAttribute('href', expect.stringContaining('judge-calibration/20261014-annotation-V/README.md'));
    expect(within(human).getByRole('link', { name: '打开标注页' })).toHaveAttribute('href', '#/lab/annotate');
  });
});
