import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkspaceView } from '../features/WorkspaceView';
import { JudgeProvenance } from '../features/JudgeProvenance';
import { TraceView } from '../features/TraceView';
import { initialConfig, toRequest } from '../state';
import type { WorkspaceConfig } from '../state';
import type { EvaluationResult, ResultRow } from '../api/types';
import { MODELS } from '../config';

const generator = MODELS[0].id;
const judge = MODELS[1].id;

describe('independently configured evaluation judge', () => {
  it('selects a judge outside tested models and clears back to the original request', () => {
    let current: WorkspaceConfig = { ...initialConfig, modelNames: [generator] };
    function Harness() {
      const [config, change] = useState(current);
      return <WorkspaceView config={config} onChange={(value) => { current = value; change(value); }}
        onRun={vi.fn()} running={false} hasLiveResult={false} error={null} configurationCheck={null}/>;
    }
    render(<Harness/>);
    fireEvent.change(screen.getByLabelText('评测裁判'), { target: { value: judge } });
    expect(current.modelNames).toEqual([generator]);
    expect(toRequest(current).judge_model_name).toBe(judge);
    expect(screen.getByText(/已单独配置裁判/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('评测裁判'), { target: { value: generator } });
    expect(screen.getByText(/仍属于同模型核对/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('评测裁判'), { target: { value: '' } });
    expect(toRequest(current)).not.toHaveProperty('judge_model_name');
  });

  it('disables a removed judge and keeps running controls disabled', () => {
    const props = { config: { ...initialConfig, judgeModelName: 'removed-judge' }, onChange: vi.fn(),
      onRun: vi.fn(), running: false, hasLiveResult: false, error: null, configurationCheck: null };
    const page = render(<WorkspaceView {...props}/>);
    expect(screen.getByRole('button', { name: '开始评测' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('当前不在模型登记中');
    page.rerender(<WorkspaceView {...props} running/>);
    expect(screen.getByLabelText('评测裁判')).toBeDisabled();
  });

  it('never substitutes a requested model for an unknown returned name', () => {
    render(<JudgeProvenance rows={[{ model: generator, judge_model: judge, judge_mode: 'configured', judge_response_models: [] }]}/>);
    const section = screen.getByRole('region', { name: '裁判来源' });
    expect(within(section).getByText('未知（响应未记录）')).toBeInTheDocument();
    expect(within(section).getByText(judge)).toBeInTheDocument();
  });

  it('does not infer provenance for frozen records without fields', () => {
    const { container } = render(<JudgeProvenance rows={[{ model: generator, faithfulness: 1 }]}/>);
    expect(container).toBeEmptyDOMElement();
  });

  it('switches models without carrying over the previous judge', () => {
    const row = (name: string): ResultRow => ({ question: '问题', answer: '回答', contexts: ['原文'],
      judge_model: name, judge_mode: 'configured', judge_response_models: [`actual-${name}`],
      hallucination_trace: { statements: [], trace_status: 'model_empty' } });
    const result: EvaluationResult = { summary: [], summary_df: [], model_dfs: { 'model-a': [row('judge-a')], 'model-b': [row('judge-b')] },
      gate_passed: false, gate_message: '', chart_path: null, html_path: null, total_elapsed_seconds: null };
    render(<TraceView result={result}/>);
    expect(screen.getByText('actual-judge-a')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'model-b' } });
    expect(screen.getByText('actual-judge-b')).toBeInTheDocument();
    expect(screen.queryByText('actual-judge-a')).not.toBeInTheDocument();
  });
});
