import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RobustnessView } from '../features/RobustnessView';
import { parseLabHash } from '../labRoute';
import data from '../fixtures/robustness.json';

const region = (name: string) => screen.getByRole('region', { name });

describe('Robustness check-up view', () => {
  it('lists the five evidence conditions with how many questions could be built', () => {
    render(<RobustnessView/>);
    const conditions = region('五种资料条件');
    const row = (name: string) => [...within(conditions).getByRole('rowheader', { name }).closest('tr')!.querySelectorAll('td')].map((cell) => cell.textContent);
    expect(row('移除依据')).toEqual(['去掉含依据的资料，用其他题的资料补足条数', '标出无依据', '拒答', '20 / 20']);
    expect(row('反事实').at(-1)).toBe('13 / 207 题依据中没有可改的数字或情态词');
  });

  it('shows the judge side against its same-run baseline', () => {
    render(<RobustnessView/>);
    const judge = region('核对侧：裁判跟着证据走吗');
    expect(within(judge).getByText('57 / 58 符合预期')).toBeInTheDocument();
    expect(within(judge).getAllByText('16 / 16')).toHaveLength(3);
    expect(within(judge).getByText('9 / 10')).toBeInTheDocument();
    expect(within(judge).getByText('1 例未达预期')).toBeInTheDocument();
    expect(within(judge).getByText(/改数字 4 \/ 4，改情态词 5 \/ 6/)).toBeInTheDocument();
  });

  it('shows the generator side: refusals when evidence is removed and no memorised values', () => {
    render(<RobustnessView/>);
    const generator = region('生成侧：被测应用会不会被资料带偏');
    expect(within(generator).getByText('没有拿原值顶替资料')).toBeInTheDocument();
    expect(within(generator).getByText('19 / 20')).toBeInTheDocument();
    expect(within(generator).getByText('拒答 95%')).toBeInTheDocument();
    expect(within(generator).getByText('2 题改为作答')).toBeInTheDocument();
    expect(within(generator).getByText(/F01、F05 原样下拒答/)).toBeInTheDocument();
    expect(within(generator).getByText(/D01 没有拒答/)).toBeInTheDocument();
  });

  it('renders one matrix row per question and marks unmet expectations', () => {
    render(<RobustnessView/>);
    const matrix = within(region('逐题结果')).getByRole('table');
    expect(within(matrix).getAllByRole('rowheader')).toHaveLength(data.matrix.length);
    const d01 = within(matrix).getByRole('rowheader', { name: 'D01' }).closest('tr')!;
    const cells = [...d01.querySelectorAll('td.rb-cell')];
    expect(cells.map((cell) => cell.textContent)).toEqual(['答', '答', '答', '答', '—', '支', '支', '支', '标', '—']);
    expect(cells[3]).toHaveClass('rb-cell--warn');
    expect(cells[8]).toHaveClass('rb-cell--ok');
    expect(cells[4]).toHaveAttribute('title', 'D01 · 反事实：依据中没有可改的数字或情态词');
  });

  it('expands notable results with the statements and the author remark', () => {
    render(<RobustnessView/>);
    const notes = region('值得注意的结果');
    expect(within(notes).getAllByRole('listitem').filter((item) => item.querySelector('details'))).toHaveLength(data.notes.length);
    const d09 = within(notes).getByText('D09 · 核对侧 · 反事实').closest('details')!;
    expect(within(d09).getByText('应当 → 无需')).toBeInTheDocument();
    expect(within(d09).getAllByText('支持')).toHaveLength(2);
  });

  it('links sources to the public repository and is reachable by deep link', () => {
    render(<RobustnessView/>);
    for (const link of screen.getAllByRole('link')) expect(link.getAttribute('href')).toMatch(/^https:\/\/github\.com\/4444wjt\/RAG_Evaluation_Demo\/blob\/master\/[\w./-]+$/);
    expect(parseLabHash('#/lab/robustness')).toEqual({ target: 'lab', view: 'robustness' });
    expect(parseLabHash('#/lab/demo/robustness')).toEqual({ target: 'demo', view: 'robustness' });
  });
});
