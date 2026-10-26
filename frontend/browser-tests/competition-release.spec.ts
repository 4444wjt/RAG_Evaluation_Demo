import { expect, test } from '@playwright/test';

// The real published fixture is checked separately from pending-state test mocks.
for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 800 }]) {
  test(`published author review and six views at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const evaluations: string[] = [];
    page.on('request', request => { if (/\/api\/evaluate(?:$|\?)/.test(request.url())) evaluations.push(request.url()); });
    await page.goto('/#/lab/demo');
    await expect(page.getByRole('heading', { name: '评测总览', exact: true })).toBeVisible();
    const nav = page.getByRole('navigation', { name: '工作区导航' });
    const views = ['评测工作区', '多模型对比', '幻觉分析', '评测报告', '评测验证', '评测总览'];
    for (const name of views) {
      await page.getByRole('tab', { name: name === '评测验证' ? '验证与审计' : '评测', exact: true }).click();
      await nav.getByRole('button', { name, exact: true }).click();
      await expect(page.getByRole('heading', { name, exact: true }).first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await page.getByRole('tab', { name: '验证与审计', exact: true }).click();
    await nav.getByRole('button', { name: '评测验证', exact: true }).click();
    await expect(page.locator('[data-review-record]')).toHaveCount(13);
    await page.getByLabel('核验状态', { exact: true }).selectOption('agree');
    await expect(page.locator('[data-review-record]')).toHaveCount(3);
    await page.getByLabel('核验状态', { exact: true }).selectOption('disagree');
    await expect(page.locator('[data-review-record]')).toHaveCount(5);
    await page.getByLabel('核验状态', { exact: true }).selectOption('uncertain');
    await expect(page.locator('[data-review-record]')).toHaveCount(5);
    await page.getByLabel('核验状态', { exact: true }).selectOption('pending');
    await expect(page.locator('[data-review-record]')).toHaveCount(0);
    await page.getByLabel('核验状态', { exact: true }).selectOption('all');
    await page.screenshot({ path: `test-results/competition/verification-${viewport.width}x${viewport.height}.png`, fullPage: true });
    expect(evaluations).toEqual([]);
  });
}
