import { expect, test } from '@playwright/test';

const generator = 'Pro/Qwen/Qwen2.5-7B-Instruct';
const judge = 'deepseek-chat';
const row = {
  question: '测试用问题', answer: '测试用回答', contexts: ['测试用原文'], ground_truth: '测试用参考', case_type: 'positive',
  faithfulness: 0.9, context_precision: 0.9, answer_relevancy: 0.9, answer_correctness: 0.9, context_recall: 0.9,
  judge_model: judge, judge_mode: 'configured', judge_response_models: ['offline-actual-judge'], judge_response_scope: 'model_batch',
  hallucination_trace: JSON.stringify({ statements: [{ text: '测试用回答', verdict: 'supported', evidence: '测试用原文' }], trace_status: 'ok' }),
};
const result = {
  summary: [{ model: generator, ...row, pos_faithfulness: 0.9, pos_context_precision: 0.9 }], summary_df: [],
  model_dfs: { [generator]: [row] }, gate_passed: true, gate_message: '离线浏览器测试',
  chart_path: null, html_path: null, total_elapsed_seconds: 0,
};

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 800 }]) {
  test(`judge configuration and result provenance at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const requests: Record<string, unknown>[] = [];
    await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/health') return route.fulfill({ json: { status: 'ok' } });
      if (path === '/api/models') return route.fulfill({ json: { models: [], localFile: 'models.local.json' } });
      if (path === '/api/evaluate') {
        requests.push(route.request().postDataJSON() as Record<string, unknown>);
        return route.fulfill({ json: result });
      }
      throw new Error(`Unexpected API request: ${path}`);
    });
    await page.goto('/#/lab/workspace');
    await page.locator('.model-card').nth(0).click();
    await page.locator('.model-card').nth(2).click();
    const select = page.getByLabel('评测裁判', { exact: true });
    await select.selectOption(judge);
    await expect(select).toHaveValue(judge);
    await expect(page.getByText(/已单独配置裁判/)).toBeVisible();
    await select.focus();
    await expect(select).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await select.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/judge-workspace-${viewport.width}x${viewport.height}.png`, fullPage: true });
    await page.getByRole('button', { name: '开始评测', exact: true }).click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0]).toMatchObject({ model_names: [generator], judge_model_name: judge });
    await page.getByRole('button', { name: '幻觉分析', exact: true }).click();
    const provenance = page.getByRole('region', { name: '裁判来源' });
    await expect(provenance).toContainText('offline-actual-judge');
    await expect(provenance).toContainText('本模型批次');
    await provenance.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/judge-trace-${viewport.width}x${viewport.height}.png`, fullPage: true });
    await page.getByRole('button', { name: '评测报告', exact: true }).click();
    await expect(page.getByRole('region', { name: '裁判来源' })).toContainText('offline-actual-judge');
    expect(requests).toHaveLength(1);
  });
}
