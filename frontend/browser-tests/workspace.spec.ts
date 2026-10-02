import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import type { EvaluationResult } from '../src/api/types';

const fixture = JSON.parse(readFileSync(new URL('../src/fixtures/demo.json', import.meta.url), 'utf8')) as { result: EvaluationResult };

test.beforeEach(async ({ page }) => {
  await page.route('http://127.0.0.1:5173/api/**', async (route) => {
    const health = route.request().url().endsWith('/health');
    await route.fulfill({ status: health ? 200 : 500, contentType: 'application/json', body: JSON.stringify(health ? { status: 'ok' } : { detail: 'Offline test blocks unmocked evaluations' }) });
  });
});

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 1080 }, { width: 390, height: 844 }]) {
  test(`all views fit ${viewport.width}px and demo never posts`, async ({ page }) => {
    let posts = 0;
    page.on('request', (request) => { if (request.method() === 'POST') posts += 1; });
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.setViewportSize(viewport);
    await page.goto('/');
    await expect(page.getByText('暂无评测结果', { exact: true })).toBeVisible();
    await page.screenshot({ path: `test-results/${viewport.width}-empty.png`, fullPage: true });
    await page.getByRole('button', { name: '加载演示', exact: true }).click();
    await expect(page.getByText('PASS', { exact: true })).toBeVisible();
    await expect(page.locator('.evaluation-chart-canvas svg')).toBeVisible();
    if (viewport.width === 1920) {
      const chart = await page.locator('.evaluation-chart').boundingBox();
      expect(chart!.y + chart!.height).toBeLessThanOrEqual(viewport.height);
    }
    for (const name of ['评测总览', '评测工作区', '多模型对比', '幻觉分析', '评测报告']) {
      await page.getByRole('navigation').getByRole('button', { name, exact: true }).click();
      await expect(page.locator('h1')).toHaveCount(1);
      await expect(page.getByRole('heading', { name, level: 1, exact: true })).toBeVisible();
      const titleSize = await page.locator('h1').evaluate((element) => parseFloat(getComputedStyle(element).fontSize));
      expect(titleSize).toBeGreaterThanOrEqual(26);
      const width = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(width).toBeLessThanOrEqual(viewport.width + 1);
      await page.screenshot({ path: `test-results/${viewport.width}-${name}.png`, fullPage: true });
    }
    expect(posts).toBe(0);
    expect(errors).toEqual([]);
  });
}

for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
  test(`summary model selection updates all KPIs at ${viewport.width}px without requesting the API`, async ({ page }) => {
    let posts = 0;
    let apiRequests = 0;
    page.on('request', (request) => {
      if (request.method() === 'POST') posts += 1;
      if (request.url().includes('/api/')) apiRequests += 1;
    });
    await page.setViewportSize(viewport);
    await page.goto('/');
    await expect(page.getByText('服务可连接', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '加载演示', exact: true }).click();
    const selector = page.getByRole('combobox', { name: '当前摘要模型', exact: true });
    await expect(selector).toBeVisible();
    await expect(selector).toHaveValue('deepseek-chat');
    const before = apiRequests;
    const models = [
      { id: 'deepseek-chat', label: 'DeepSeek', values: ['0', '0', '0', '100.0%'], gate: 'PASS' },
      { id: 'Pro/Qwen/Qwen2.5-7B-Instruct', label: 'Qwen2.5-7B', values: ['3', '0', '3', '100.0%'], gate: 'FAIL' },
      { id: 'glm-5.2-fast-preview', label: 'GLM-5.2-Fast', values: ['0', '0', '0', '100.0%'], gate: 'PASS' },
    ];
    const labels = ['幻觉语句数', '事实错误数', '无依据编造数', '拒答准确率'];
    for (const model of models) {
      await selector.selectOption(model.id);
      await expect(selector).toHaveValue(model.id);
      expect(await selector.evaluate((element: HTMLSelectElement) => element.selectedOptions[0].textContent)).toBe(model.label);
      for (const [index, label] of labels.entries()) {
        await expect(page.locator('.metric-tile').filter({ hasText: label }).locator('strong')).toHaveText(model.values[index]);
      }
      await expect(page.getByText(model.gate, { exact: true })).toBeVisible();
      await expect(page.locator('.original-gate')).toHaveText('原始评测门禁：PASS');
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width + 1);
      if (model.label === 'Qwen2.5-7B') {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: `test-results/summary-model-${viewport.width}.png`, fullPage: true });
      }
    }
    expect(apiRequests).toBe(before);
    expect(posts).toBe(0);
  });
}

test('first model column remains opaque and fixed while table scrolls', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1080 });
  await page.goto('/');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await page.getByRole('navigation').getByRole('button', { name: '多模型对比', exact: true }).click();
  const wrapper = page.locator('.comparison-table-scroll');
  await wrapper.scrollIntoViewIfNeeded();
  const header = wrapper.locator('thead th').first();
  const cell = wrapper.locator('tbody th[scope="row"]').first();
  const before = { header: await header.boundingBox(), cell: await cell.boundingBox() };
  await wrapper.evaluate((element) => { element.scrollLeft = 680; });
  await expect.poll(() => wrapper.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  const after = { header: await header.boundingBox(), cell: await cell.boundingBox() };
  expect(after.header!.x).toBeCloseTo(before.header!.x, 0);
  expect(after.cell!.x).toBeCloseTo(before.cell!.x, 0);
  for (const element of [header, cell]) {
    expect(await element.evaluate((node) => getComputedStyle(node).position)).toBe('sticky');
    expect(await element.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
  }
});

test('current model gate follows positive metrics while preserving the original gate and the whole comparison chart', async ({ page }) => {
  let apiRequests = 0;
  let posts = 0;
  page.on('request', (request) => {
    if (request.url().includes('/api/')) apiRequests += 1;
    if (request.method() === 'POST') posts += 1;
  });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/');
  await expect(page.getByText('服务可连接', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await expect(page.locator('.evaluation-chart-canvas svg')).toBeVisible();
  const chartBeforeSelection = await page.locator('.evaluation-chart-canvas svg').innerHTML();
  const requestsBeforeSelection = apiRequests;
  const selector = page.getByRole('combobox', { name: '当前摘要模型', exact: true });
  for (const model of [
    { id: 'deepseek-chat', label: 'DeepSeek', gate: 'PASS', faith: '100.0%' },
    { id: 'Pro/Qwen/Qwen2.5-7B-Instruct', label: 'Qwen2.5-7B', gate: 'FAIL', faith: '33.3%' },
    { id: 'glm-5.2-fast-preview', label: 'GLM-5.2-Fast', gate: 'PASS', faith: '100.0%' },
  ]) {
    await selector.selectOption(model.id);
    await expect(page.locator('.gate-value')).toHaveText(model.gate);
    await expect(page.locator('.gate-label')).toHaveText('当前模型质量门禁');
    await expect(page.locator('.gate-subject dd')).toHaveText(model.label);
    await expect(page.locator('.gate-range dd')).toHaveText('3 条 positive 用例');
    await expect(page.locator('[data-gate-field="pos_faithfulness"] dd > span')).toHaveText(model.faith);
    await expect(page.locator('[data-gate-field="pos_context_precision"] dd > span')).toHaveText('100.0%');
    for (const metric of ['pos_faithfulness', 'pos_context_precision']) {
      await expect(page.locator(`[data-gate-field="${metric}"] dd > small`)).toHaveText('门槛 ≥ 80.0%');
    }
    await expect(page.locator('.original-gate')).toHaveText('原始评测门禁：PASS');
    await expect(page.locator('.gate-interpretation')).toHaveText('门禁结论仅反映规则检查，不代表所有模型指标优秀。');
    await expect(page.locator('.result-context-note')).toHaveText('当前模型用于指标查看，不代表最佳模型；门禁展示按该模型的正向指标及已有阈值检查，原始记录保持不变。');
    await expect(page.locator('.ability-section .section-heading p')).toHaveText('图中整体均值用于模型横向比较；当前模型门禁仅检查正向忠实度与正向上下文精度。');
    expect(await page.locator('.evaluation-chart-canvas svg').innerHTML()).toBe(chartBeforeSelection);
  }
  await selector.selectOption('Pro/Qwen/Qwen2.5-7B-Instruct');
  expect(fixture.result.summary[1]).toMatchObject({ faithfulness: 0.25, answer_correctness: 0.2527, context_recall: 0.3333 });
  await expect(page.locator('.gate-reason')).toHaveText('正向忠实度低于门槛');
  expect(await page.locator('.gate-value').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(248, 113, 113)');
  const details = page.locator('.gate-details');
  await details.locator('summary').click();
  await expect(details).toContainText('原始门禁说明未记录。');
  await expect(details).toContainText('原始门禁采用首个有效评测模型的正向指标；当前卡片检查所选模型，不改写原始记录。');
  await expect(details).toContainText('已有阈值来自演示快照的原始运行记录；缺失项不作推断。');
  await details.locator('summary').click();
  for (const width of [1920, 390]) {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 844 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator('.gate-context')).toBeVisible();
    await expect(page.locator('.gate-interpretation')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
    await page.screenshot({ path: `test-results/phase5c-current-model-gate-${width}.png`, fullPage: true });
  }
  expect(apiRequests).toBe(requestsBeforeSelection);
  expect(posts).toBe(0);
});

test('mock evaluation preserves zero thresholds and original gate=false while completing a current PASS', async ({ page }) => {
  const posts: unknown[] = [];
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  const result = structuredClone(fixture.result);
  result.gate_passed = false;
  result.summary[0].hallucination_statement_count = 17;
  result.summary[0].trace_fact_error_count = null;
  await page.route('**/api/evaluate', async (route) => {
    posts.push(route.request().postDataJSON());
    await wait;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await page.getByRole('button', { name: '新建评测', exact: true }).click();
  await page.getByRole('checkbox', { name: '沿用后端默认阈值' }).uncheck();
  await page.getByRole('spinbutton', { name: '忠实度门禁阈值', exact: true }).fill('0');
  await page.getByRole('spinbutton', { name: '上下文精度门禁阈值', exact: true }).fill('0');
  await page.getByRole('button', { name: '开始评测', exact: true }).click();
  await expect(page.getByRole('button', { name: '评测中', exact: true })).toBeDisabled();
  await expect(page.getByText('上次结果 · 本次尚未更新')).toBeVisible();
  await expect(page.getByRole('button', { name: '加载演示', exact: true })).toBeDisabled();
  expect(posts).toHaveLength(1);
  expect(posts[0]).toMatchObject({ faith_threshold: 0, ctx_prec_threshold: 0, version_label: null });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/phase5b-running.png', fullPage: true });
  release();
  await expect(page.getByText('PASS', { exact: true })).toBeVisible();
  await expect(page.locator('.original-gate')).toHaveText('原始评测门禁：FAIL');
  await expect(page.locator('.gate-subject dd')).toHaveText('DeepSeek');
  for (const metric of ['pos_faithfulness', 'pos_context_precision']) {
    await expect(page.locator(`[data-gate-field="${metric}"] dd > small`)).toHaveText('门槛 ≥ 0.0%');
  }
  await expect(page.locator('.gate-interpretation')).toHaveText('门禁结论仅反映规则检查，不代表所有模型指标优秀。');
  await expect(page.locator('.metric-tile').filter({ hasText: '幻觉语句数' })).toContainText('17');
  await expect(page.locator('.metric-tile').filter({ hasText: '事实错误数' }).locator('strong')).toHaveText('暂无数据');
  await expect(page.getByText('真实评测结果', { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/phase5c-zero-threshold-original-fail.png', fullPage: true });
  expect(posts).toHaveLength(1);
});

test('model cards, manual sliders and reset remain usable from keyboard', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: '新建评测', exact: true }).click();
  const model = page.getByRole('button', { name: '选择 DeepSeek', exact: true });
  await model.focus();
  await page.keyboard.press('Space');
  await expect(model).toHaveAttribute('aria-pressed', 'false');
  expect(await model.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe('solid');
  expect(await model.evaluate((element) => getComputedStyle(element).transitionDuration)).toBe('0s');
  await page.keyboard.press('Enter');
  await expect(model).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('checkbox', { name: '沿用后端默认阈值' }).uncheck();
  const input = page.getByRole('spinbutton', { name: '忠实度门禁阈值', exact: true });
  await input.fill('0.25');
  await expect(page.getByRole('slider', { name: '忠实度门禁阈值滑条', exact: true })).toHaveValue('0.25');
  await page.getByRole('button', { name: '重置忠实度门禁阈值', exact: true }).click();
  await expect(input).toHaveValue('0.8');
});

test('HTML report is read locally, isolated, and cannot execute or fetch external resources', async ({ page }) => {
  let posts = 0;
  const outside: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'POST') posts += 1;
    if (request.url().includes('example.invalid')) outside.push(request.url());
  });
  await page.goto('/');
  await page.getByRole('navigation').getByRole('button', { name: '评测报告', exact: true }).click();
  await page.getByLabel('选择本地 HTML 报告', { exact: true }).setInputFiles({ name: 'local-report.html', mimeType: 'text/html', buffer: Buffer.from('<html><head><style>body{color:#223344}</style></head><body><h1>离线报告</h1><script>parent.document.body.dataset.reportExecuted="yes"</script><img src="https://example.invalid/image.png"><a href="https://example.invalid">remote</a></body></html>') });
  const frame = page.locator('iframe');
  await expect(frame).toHaveAttribute('sandbox', '');
  await expect(page.getByText('手动导入，未自动核验与当前结果对应', { exact: true })).toBeVisible();
  await expect(page.frameLocator('iframe').getByRole('heading', { name: '离线报告' })).toBeVisible();
  expect(await page.locator('body').getAttribute('data-report-executed')).toBeNull();
  expect(await frame.getAttribute('srcdoc')).toContain("script-src 'none'");
  expect(await frame.getAttribute('srcdoc')).not.toContain('<script>');
  expect(posts).toBe(0);
  expect(outside).toEqual([]);
});

test('check configuration gives visible feedback on the same page, calls health only and can retry', async ({ page }) => {
  let evaluations = 0;
  let healthChecks = 0;
  let connected = true;
  let release!: () => void;
  let pending: Promise<void> | null = null;
  await page.route('http://127.0.0.1:5173/api/**', async (route) => {
    if (route.request().url().endsWith('/health')) {
      healthChecks += 1;
      if (pending) await pending;
      await route.fulfill({ status: connected ? 200 : 503, contentType: 'application/json', body: JSON.stringify(connected ? { status: 'ok' } : {}) });
    } else {
      evaluations += 1;
      await route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
    }
  });
  await page.goto('/');
  await expect(page.getByText('服务可连接', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '新建评测', exact: true }).click();
  await page.getByRole('button', { name: '开始评测', exact: true }).click();
  await expect(page.getByText('本次评测未完成', { exact: true })).toBeVisible();
  const before = healthChecks;
  connected = false;
  pending = new Promise<void>((resolve) => { release = resolve; });
  await page.getByRole('button', { name: '检查配置', exact: true }).click();
  await expect(page.getByRole('button', { name: '检查中…', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '开始评测', exact: true })).toBeDisabled();
  const feedback = page.getByRole('status', { name: '配置检查结果' });
  await expect(feedback).toContainText('正在校验本地参数');
  await expect(feedback).toBeFocused();
  await expect.poll(() => healthChecks).toBe(before + 1);
  release();
  pending = null;
  await expect(feedback).toContainText('服务未连接');
  await expect(page.getByRole('button', { name: '检查配置', exact: true })).toBeEnabled();
  connected = true;
  await page.getByRole('button', { name: '检查配置', exact: true }).click();
  await expect(feedback).toContainText('本地参数检查通过，服务可连接。');
  await expect(feedback).toContainText('不会验证文件是否存在、模型或凭据是否可用');
  expect(evaluations).toBe(1);
  expect(healthChecks).toBe(before + 2);
  await page.screenshot({ path: 'test-results/configuration-check.png', fullPage: true });
});

for (const mode of ['auto', 'custom']) {
  test(`cleared benchmark size does not break switching to ${mode}`, async ({ page }) => {
    const posts: Record<string, unknown>[] = [];
    await page.route('http://127.0.0.1:5173/api/evaluate', async (route) => {
      posts.push(route.request().postDataJSON());
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture.result) });
    });
    await page.goto('/');
    await page.getByRole('button', { name: '新建评测', exact: true }).click();
    await page.getByRole('combobox', { name: '测试集来源', exact: true }).selectOption('benchmark');
    await page.getByRole('spinbutton', { name: '基准样本数量', exact: true }).fill('');
    await page.getByRole('combobox', { name: '测试集来源', exact: true }).selectOption(mode);
    await page.getByRole('button', { name: '开始评测', exact: true }).click();
    await expect(page.getByText('WARNING', { exact: true })).toBeVisible();
    await expect(page.locator('.original-gate')).toHaveText('原始评测门禁：PASS');
    expect(await page.locator('.gate-value').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(245, 158, 11)');
    for (const metric of ['pos_faithfulness', 'pos_context_precision']) {
      await expect(page.locator(`[data-gate-field="${metric}"] dd > small`)).toHaveText('门槛未提供');
    }
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatchObject({ benchmark: null, benchmark_limit: 20, testset_path: mode === 'custom' ? 'testset_example.csv' : null });
  });
}

test('Gold design tokens keep primary actions distinct from neutral data and workspace surfaces', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/');
  const tokens = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(['--brand', '--accent', '--info', '--success', '--warning', '--danger', '--demo', '--bg-page', '--bg-workspace', '--bg-surface', '--bg-elevated', '--text-primary', '--text-secondary', '--text-muted'].map((name) => [name, style.getPropertyValue(name).trim().toLowerCase()]));
  });
  expect(tokens).toMatchObject({
    '--brand': '#f2c879', '--accent': '#f2c879', '--info': '#59c3d1',
    '--success': '#34d399', '--warning': '#f59e0b', '--danger': '#f87171', '--demo': '#f2c879',
    '--bg-page': '#080b10', '--bg-workspace': '#10161f', '--bg-surface': '#151b24', '--bg-elevated': '#1b2430',
    '--text-primary': '#f5f7fa', '--text-secondary': '#a7b0be', '--text-muted': '#707887',
  });
  expect(tokens['--demo']).not.toBe(tokens['--warning']);
  expect(await page.locator('html').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(8, 11, 16)');
  expect(await page.locator('body').evaluate((node) => getComputedStyle(node).fontSize)).toBe('15px');
  expect(await page.locator('h1').evaluate((node) => getComputedStyle(node).fontSize)).toBe('32px');
  const primary = page.getByRole('button', { name: '新建评测', exact: true });
  expect(await primary.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(242, 200, 121)');
  expect(await primary.evaluate((node) => getComputedStyle(node).color)).toBe('rgb(8, 11, 16)');
  expect(await page.locator('.nav-item.active').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(242, 200, 121)');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  expect(await page.locator('.ability-section').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(21, 27, 36)');
  expect(await page.locator('.ability-section h2').evaluate((node) => getComputedStyle(node).fontSize)).toBe('20px');
  expect(await page.locator('.gate-value').evaluate((node) => getComputedStyle(node).fontSize)).toBe('48px');
  expect(await page.locator('.gate-value').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(52, 211, 153)');
  for (const metric of await page.locator('.metric-tile strong').all()) {
    expect(await metric.evaluate((node) => getComputedStyle(node).color)).toBe('rgb(245, 247, 250)');
    expect(await metric.evaluate((node) => getComputedStyle(node).fontSize)).toBe('38px');
  }
  await primary.click();
  expect(await page.locator('.experiment-workspace').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(16, 22, 31)');
  expect(await page.locator('.stage-content').first().evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(21, 27, 36)');
  for (const model of await page.locator('.model-card.selected').all()) {
    expect(['rgb(16, 22, 31)', 'rgb(21, 27, 36)', 'rgb(27, 36, 48)']).toContain(await model.evaluate((node) => getComputedStyle(node).backgroundColor));
    expect(await model.locator('.model-check.checked').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(242, 200, 121)');
  }
  await page.getByRole('checkbox', { name: '沿用后端默认阈值' }).uncheck();
  expect(await page.getByRole('spinbutton', { name: '忠实度门禁阈值', exact: true }).evaluate((node) => getComputedStyle(node).color)).toBe('rgb(245, 247, 250)');
});

test('Demo Gold, Warning Amber, Cyan running and Red failure remain separate visual roles', async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const result = structuredClone(fixture.result);
  result.gate_passed = false;
  result.summary[0].pos_faithfulness = 0.3333;
  await page.route('**/api/evaluate', async (route) => {
    await pending;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  expect(await page.locator('.badge.demo').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(242, 200, 121)');
  await page.getByRole('button', { name: '新建评测', exact: true }).click();
  await page.getByRole('checkbox', { name: '沿用后端默认阈值' }).uncheck();
  await page.getByRole('button', { name: '开始评测', exact: true }).click();
  const running = page.getByRole('button', { name: '评测中', exact: true });
  await expect(running).toBeDisabled();
  expect(await page.locator('.badge.warning').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(245, 158, 11)');
  expect(await page.locator('.badge.demo').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(242, 200, 121)');
  expect(await page.locator('.busy-dot').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(89, 195, 209)');
  const disabledBackground = await running.evaluate((node) => getComputedStyle(node).backgroundColor);
  await running.hover();
  expect(await running.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe(disabledBackground);
  release();
  await expect(page.getByText('FAIL', { exact: true })).toBeVisible();
  await expect(page.locator('.original-gate')).toHaveText('原始评测门禁：FAIL');
  await expect(page.locator('.gate-reason')).toHaveText('正向忠实度低于门槛');
  expect(await page.locator('.gate-value').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(248, 113, 113)');
  await expect(page.locator('.badge.warning')).toHaveCount(0);
});

test('Trace verdicts preserve semantics while selection and evidence use restrained theme roles', async ({ page }) => {
  const result = structuredClone(fixture.result);
  const model = Object.keys(result.model_dfs)[0];
  result.model_dfs[model][0].hallucination_trace = JSON.stringify({ statements: [
    { text: '支持陈述', verdict: 'supported', evidence: '支持证据' },
    { text: '事实错误陈述', verdict: 'fact_error', evidence: '纠正证据' },
    { text: '编造陈述', verdict: 'fabrication', evidence: '' },
    { text: '未知陈述', verdict: 'unrecognized', evidence: '' },
  ] });
  await page.route('**/api/evaluate', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '新建评测', exact: true }).click();
  await page.getByRole('button', { name: '开始评测', exact: true }).click();
  await expect(page.getByText('WARNING', { exact: true })).toBeVisible();
  await expect(page.locator('.original-gate')).toHaveText('原始评测门禁：PASS');
  await page.getByRole('navigation').getByRole('button', { name: '幻觉分析', exact: true }).click();
  const verdicts = [
    { kind: 'supported', text: '有依据', color: 'rgb(52, 211, 153)' },
    { kind: 'fact_error', text: '事实错误', color: 'rgb(248, 113, 113)' },
    { kind: 'fabrication', text: '无依据编造', color: 'rgb(248, 113, 113)' },
    { kind: 'unknown', text: '未知判定', color: 'rgb(167, 176, 190)' },
  ];
  for (const verdict of verdicts) {
    const badge = page.locator(`.trace-verdict--${verdict.kind}`);
    await expect(badge).toHaveText(verdict.text);
    expect(await badge.evaluate((node) => getComputedStyle(node).color)).toBe(verdict.color);
  }
  const selected = page.locator('.trace-case.is-selected');
  expect(['rgb(16, 22, 31)', 'rgb(21, 27, 36)', 'rgb(27, 36, 48)']).toContain(await selected.evaluate((node) => getComputedStyle(node).backgroundColor));
  expect(await selected.evaluate((node) => getComputedStyle(node).borderLeftColor)).toBe('rgb(242, 200, 121)');
  expect(await page.locator('.trace-model-id').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(89, 195, 209)');
  expect(await page.locator('.trace-answer').evaluate((node) => getComputedStyle(node).fontSize)).toBe('15px');
  expect(await page.locator('.trace-evidence blockquote').first().evaluate((node) => getComputedStyle(node).fontSize)).toBe('14px');
});
