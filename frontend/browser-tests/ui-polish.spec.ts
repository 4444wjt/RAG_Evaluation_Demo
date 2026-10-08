import { test, expect, type Page } from '@playwright/test';

async function themeBackground(page: Page, token: string) {
  return page.evaluate((name) => {
    const probe = document.createElement('span');
    probe.style.backgroundColor = `var(${name})`;
    document.body.append(probe);
    const color = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return color;
  }, token);
}

test.beforeEach(async ({ page }) => {
  await page.route('http://127.0.0.1:5173/api/**', async (route) => {
    const health = route.request().url().endsWith('/health');
    await route.fulfill({ status: health ? 200 : 500, contentType: 'application/json', body: JSON.stringify(health ? { status: 'ok' } : { detail: 'UI checks block unmocked evaluations' }) });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 800 }, { width: 1280, height: 1080 }]) {
  test(`desktop conclusion and KPIs have independent hierarchy at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/#/lab');
    await page.getByRole('button', { name: '加载演示', exact: true }).click();
    const gate = page.getByRole('region', { name: '当前模型质量门禁', exact: true });
    const metrics = page.getByRole('region', { name: '评测核心指标', exact: true });
    const gateBox = (await gate.boundingBox())!;
    const metricBox = (await metrics.boundingBox())!;
    expect(Math.abs(gateBox.width - metricBox.width)).toBeLessThan(1);
    expect(gateBox.y + gateBox.height).toBeLessThan(metricBox.y);
    expect(metricBox.height).toBeLessThan(180);
    await expect(metrics.locator('.quality-gate')).toHaveCount(0);
    await expect(metrics.locator('.metric-tile')).toHaveCount(4);
    await expect(gate).toHaveCSS('background-color', await themeBackground(page, '--bg-surface'));
    await expect(gate.locator('.gate-status-block')).toHaveCSS('background-color', await themeBackground(page, '--success-surface'));
    await expect(gate.locator('.gate-value')).toHaveCSS('font-size', '48px');
    await expect(gate.locator('.gate-subject dd')).toHaveCSS('font-size', '16px');
    await expect(gate.locator('.gate-range dd')).toHaveCSS('font-size', '14px');
    await expect(gate.locator('.gate-reason')).toHaveCSS('font-size', '14px');
    for (const field of ['pos_faithfulness', 'pos_context_precision']) {
      await expect(gate.locator(`[data-gate-field="${field}"] dd > span`)).toHaveCSS('font-size', '24px');
      await expect(gate.locator(`[data-gate-field="${field}"] dd > small`)).toHaveText('门槛 ≥ 80.0%');
    }
    const keyMetrics = await gate.locator('.gate-metrics > div').all();
    expect(Math.abs((await keyMetrics[0].boundingBox())!.y - (await keyMetrics[1].boundingBox())!.y)).toBeLessThan(1);
    await expect(gate.locator('.gate-interpretation')).toBeVisible();
    await expect(gate.locator('.gate-details')).not.toHaveAttribute('open');
    await expect(page.locator('.ability-section .evaluation-chart-canvas')).toHaveCSS('height', '360px');
    const entries = await page.locator('.overview-bottom .analysis-entry').all();
    expect(entries).toHaveLength(2);
    expect(Math.abs((await entries[0].boundingBox())!.width - (await entries[1].boundingBox())!.width)).toBeLessThan(1);
    await page.getByRole('combobox', { name: '当前摘要模型', exact: true }).selectOption({ label: 'Qwen2.5-7B' });
    await expect(gate.locator('.gate-value')).toHaveText('FAIL');
    await expect(gate.locator('.gate-reason')).toBeVisible();
    await expect(gate.locator('.gate-reason')).toHaveText('正向忠实度低于门槛');
    await expect(gate.locator('.gate-status-block')).toHaveCSS('background-color', await themeBackground(page, '--danger-surface'));
    await page.getByRole('navigation').getByRole('button', { name: '多模型对比', exact: true }).click();
    await expect(page.locator('.evaluation-chart-canvas').first()).toHaveCSS('height', '500px');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width + 1);
  });
}

test('page actions emphasize the current task and gate details support keyboard interaction', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/#/lab');
  await expect(page.locator('.btn-primary')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '配置评测', exact: true })).toHaveClass(/btn-primary/);
  const newEvaluation = page.getByRole('button', { name: '新建评测', exact: true });
  await expect(newEvaluation).toHaveClass(/btn-secondary/);
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await expect(newEvaluation).toHaveClass(/btn-primary/);
  const details = page.locator('.gate-details');
  const summary = details.locator('summary');
  await summary.hover();
  await expect(summary).toHaveCSS('background-color', await themeBackground(page, '--bg-hover'));
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab');
  await summary.focus();
  expect(await summary.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
  await expect(summary).toHaveCSS('outline-color', 'rgb(242, 200, 121)');
  await expect(summary).toHaveCSS('outline-offset', '-3px');
  await page.keyboard.press('Enter');
  await expect(details).toHaveAttribute('open');
  await expect(details.locator('.original-gate')).toBeVisible();
  await page.keyboard.press('Space');
  await expect(details).not.toHaveAttribute('open');
  await newEvaluation.click();
  await expect(newEvaluation).toHaveClass(/btn-secondary/);
  await expect(page.locator('.btn-primary')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '开始评测', exact: true })).toHaveClass(/btn-primary/);
  for (const field of await page.locator('.workspace-field').all()) {
    await expect(field).toHaveCSS('border-width', '0px');
    await expect(field).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  }
  await page.getByRole('navigation').getByRole('button', { name: '评测报告', exact: true }).click();
  await expect(newEvaluation).toHaveClass(/btn-secondary/);
  await expect(page.locator('.btn-primary')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '导入 HTML 报告', exact: true })).toHaveClass(/btn-primary/);
});

for (const width of [1920, 1280, 1024, 390, 320]) {
  test(`model cards keep full labels and fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1080 });
    await page.goto('/#/lab');
    await page.getByRole('button', { name: '新建评测', exact: true }).click();
    await expect(page.locator('.model-avatar')).toHaveCount(0);
    const grid = await page.locator('.model-grid').boundingBox();
    for (const name of ['DeepSeek', 'Qwen2.5-7B', 'GLM-5.2-Fast']) {
      const card = page.getByRole('button', { name: `选择 ${name}`, exact: true });
      const label = card.locator('.model-identity strong');
      await expect(label).toHaveText(name);
      await expect(label).toHaveAttribute('title', name);
      await expect(label).toHaveCSS('white-space', 'nowrap');
      await expect(label).toHaveCSS('text-overflow', 'ellipsis');
      expect((await card.boundingBox())!.width).toBeGreaterThanOrEqual(Math.min(240, grid!.width) - 1);
      if (width >= 390) {
        expect(await label.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
      }
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
  });
}

test('model hover, selected and keyboard focus have distinct feedback', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1080 });
  await page.goto('/#/lab');
  await page.getByRole('button', { name: '新建评测', exact: true }).click();
  const model = page.getByRole('button', { name: '选择 DeepSeek', exact: true });
  await page.mouse.move(0, 0);
  await expect(model).toHaveAttribute('aria-pressed', 'true');
  await expect(model).toHaveCSS('background-color', await themeBackground(page, '--bg-selected'));
  await model.hover();
  await expect(model).toHaveCSS('background-color', await themeBackground(page, '--bg-selected-hover'));
  await expect(model).toHaveCSS('border-top-color', 'rgb(242, 200, 121)');
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab');
  await model.focus();
  expect(await model.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
  await expect(model).toHaveCSS('outline-color', 'rgb(242, 200, 121)');
  await expect(model).toHaveCSS('outline-width', '2px');
  await expect(model).toHaveCSS('outline-offset', '3px');
  await page.keyboard.press('Space');
  await expect(model).toHaveAttribute('aria-pressed', 'false');
  await expect(model).toHaveCSS('background-color', await themeBackground(page, '--bg-control'));
  await model.hover();
  await expect(model).toHaveCSS('background-color', await themeBackground(page, '--bg-hover'));
  await expect(model).toHaveAttribute('aria-pressed', 'false');
  await page.mouse.move(0, 0);
  await page.keyboard.press('Enter');
  await expect(model).toHaveAttribute('aria-pressed', 'true');
});

test('controls use consistent standard, compact and icon sizes', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1080 });
  await page.goto('/#/lab');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await expect(page.locator('.summary-model-control select')).toHaveCSS('height', '32px');
  for (const selector of ['.sidebar', '.topbar']) {
    await expect(page.locator(selector)).toHaveCSS('background-color', 'rgb(16, 22, 31)');
  }
  await page.getByRole('navigation').getByRole('button', { name: '评测工作区', exact: true }).click();
  for (const control of await page.locator('.dataset-grid input, .dataset-grid select').all()) {
    await expect(control).toHaveCSS('height', '40px');
    await expect(control).toHaveCSS('border-radius', '8px');
  }
  await page.getByRole('checkbox', { name: '沿用后端默认阈值' }).uncheck();
  for (const input of await page.locator('.parameter-value input').all()) {
    await expect(input).toHaveCSS('height', '32px');
  }
  for (const button of await page.locator('.icon-button, .reset-button').all()) {
    await expect(button).toHaveCSS('width', '32px');
    await expect(button).toHaveCSS('height', '32px');
    await expect(button).toHaveCSS('border-radius', '8px');
  }
  await expect(page.locator('.run-button')).toHaveCSS('min-height', '40px');
  await page.getByRole('navigation').getByRole('button', { name: '幻觉分析', exact: true }).click();
  await expect(page.locator('.trace-model-select')).toHaveCSS('height', '32px');
});

test('Trace keyboard focus stays inside the scroll container', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 1080 });
  await page.goto('/#/lab');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await page.getByRole('navigation').getByRole('button', { name: '幻觉分析', exact: true }).click();
  await page.locator('.trace-model-select').focus();
  await page.keyboard.press('Tab');
  const first = page.locator('.trace-case').first();
  await expect(first).toBeFocused();
  expect(await first.evaluate((element) => element.matches(':focus-visible'))).toBe(true);
  await expect(first).toHaveCSS('outline-width', '2px');
  await expect(first).toHaveCSS('outline-offset', '-2px');
  await expect(first).toHaveCSS('outline-color', 'rgb(242, 200, 121)');
  await page.keyboard.press('Tab');
  const second = page.locator('.trace-case').nth(1);
  await expect(second).toBeFocused();
  await expect(second).toHaveCSS('outline-offset', '-2px');
  const list = (await page.locator('.trace-case-list').boundingBox())!;
  const row = (await second.boundingBox())!;
  expect(row.x).toBeGreaterThanOrEqual(list.x);
  expect(row.x + row.width).toBeLessThanOrEqual(list.x + list.width);
});

test('report empty state keeps its alignment and compact copy actions on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#/lab');
  await page.getByRole('button', { name: '加载演示', exact: true }).click();
  await page.getByRole('navigation').getByRole('button', { name: '评测报告', exact: true }).click();
  const empty = page.locator('.report-empty');
  await expect(empty).toHaveCSS('align-items', 'flex-start');
  await expect(empty).toHaveCSS('text-align', 'left');
  await expect(empty).toHaveCSS('padding', '24px');
  await expect(empty).toHaveCSS('min-height', '232px');
  for (const button of await page.locator('.report-copy-button').all()) {
    await expect(button).toHaveCSS('width', '32px');
    await expect(button).toHaveCSS('height', '32px');
  }
});
