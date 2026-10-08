import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';

const demo = JSON.parse(readFileSync(new URL('../src/fixtures/demo.json', import.meta.url), 'utf8'));
// Keep pending scenarios independent of future genuine reviews in the public package.
const pendingReviews = JSON.parse(readFileSync(new URL('../src/fixtures/credibility-review.json', import.meta.url), 'utf8'));
for (const record of pendingReviews.records) Object.assign(record, { status: 'pending', reviewer: null, reviewedAt: null, answerReview: null, statementReviews: [] });
for (const check of [pendingReviews.gateRuleReview, pendingReviews.reproduction.snapshotReplay, pendingReviews.reproduction.modelRerun]) Object.assign(check, { status: 'unverified', reviewer: null, reviewedAt: null, note: null });

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 800 }]) {
  test.describe(`评测验证 ${viewport.width}×${viewport.height}`, () => {
    test.use({ viewport });
    test.beforeEach(async ({ page }) => {
      await page.route('**/api/health', route => route.fulfill({ json: { status: 'ok' } }));
      await page.route('**/src/fixtures/credibility-review.json*', route => route.fulfill({ contentType: 'application/javascript', body: `export default ${JSON.stringify(pendingReviews)};` }));
    });

    test('shows pending tasks, filters, supports keyboard inspection and preserves five views', async ({ page }) => {
      const evaluations: string[] = [];
      page.on('request', request => { if (request.url().includes('/api/evaluate')) evaluations.push(request.url()); });
      await page.goto('/#/lab/demo');
      await expect(page.locator('.gate-range dt')).toHaveText('演示门禁范围');
      await expect(page.locator('.gate-snapshot-scope')).toHaveText('当前快照包含 6 个问题、3 个模型、18 条回答。');
      await expect(page.locator('.gate-interpretation')).toContainText('不代表模型总体可靠性');
      const nav = page.getByRole('navigation', { name: '工作区导航' });
      await expect(nav.getByRole('button')).toHaveCount(6);
      await nav.getByRole('button', { name: '评测验证', exact: true }).click();
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('评测验证');
      await expect(page.locator('[data-review-record]')).toHaveCount(13);
      const scope = page.getByRole('region', { name: '人工核验范围' });
      await expect(scope).toContainText('已检查0条');
      await expect(scope).toContainText('待核验13条');
      await expect(scope).toContainText('未纳入5条');
      await expect(scope).toContainText('预选 13/18 条回答，关联 12/19 条逐句记录');
      await expect(scope).toContainText('已检查包含“无法确认”');
      await expect(page.locator('.verification-status--pending')).toHaveCount(13);
      await expect(page.getByLabel('核验状态', { exact: true }).locator('option')).toContainText(['全部状态', '待核验', '一致', '不一致', '无法确认']);
      await expect(page.locator('.verification-view')).not.toContainText('分歧');
      await expect(page.getByText('未验证', { exact: true })).toHaveCount(2);
      await expect(page.locator('.verification-view').getByText('PASS', { exact: true })).toHaveCount(0);
      const layout = await page.locator('.verification-view').evaluate(element => ({
        overflow: document.documentElement.scrollWidth > window.innerWidth,
        font: getComputedStyle(element).fontSize,
        selects: [...element.querySelectorAll('select')].map(select => select.getBoundingClientRect().height),
      }));
      expect(layout).toEqual({ overflow: false, font: '14px', selects: [40, 40] });
      await page.screenshot({ path: resolve(`test-results/credibility-refinement/verification-${viewport.width}x${viewport.height}.png`), fullPage: true });
      const failure = page.getByRole('region', { name: '历史失败案例' });
      await failure.getByText('查看问题、指标与门禁依据', { exact: true }).click();
      await expect(failure.getByRole('region', { name: '金融场景原始问题' }).locator('article')).toHaveCount(3);
      const questions = failure.getByRole('region', { name: '金融场景原始问题' }).locator('article');
      for (const article of await questions.all()) {
        await expect(article.locator(':scope > .verification-original')).toBeVisible();
        await expect(article.locator('details')).not.toHaveAttribute('open');
        expect(await article.evaluate(element => [...element.children].map(child => child.tagName))).toEqual(['H4', 'P', 'P', 'DETAILS']);
      }
      await expect(failure.getByRole('region', { name: '金融场景原始门禁' })).toContainText('正向上下文精度77.8%');
      await expect(failure.getByRole('region', { name: '金融场景原始门禁' })).toContainText('门槛 ≥ 80.0%');
      await expect(failure.getByRole('region', { name: '金融场景原始门禁' })).toContainText('第二个问题的上下文精度为 33.3%');
      await expect(failure.getByRole('region', { name: '金融场景原始门禁' })).toContainText('归档正向均值 0.7778 低于 0.8 门槛');
      await failure.screenshot({ path: resolve(`test-results/credibility-refinement/failure-case-${viewport.width}x${viewport.height}.png`) });
      const contextToggle = questions.nth(1).locator('summary');
      await contextToggle.focus();
      await page.keyboard.press('Enter');
      await expect(questions.nth(1).locator('details')).toHaveAttribute('open', '');
      await expect(questions.nth(1).getByRole('heading', { name: '参考答案', exact: true })).toBeVisible();
      await page.keyboard.press('Enter');
      await expect(questions.nth(1).locator('details')).not.toHaveAttribute('open');
      await failure.getByText('查看问题、指标与门禁依据', { exact: true }).click();
      await page.getByLabel('模型', { exact: true }).selectOption('Pro/Qwen/Qwen2.5-7B-Instruct');
      await expect(page.locator('[data-review-record]')).toHaveCount(4);
      const button = page.getByRole('button', { name: '查看Qwen2.5-7B用例 3 的核验详情' });
      await page.keyboard.press('Tab');
      await button.focus();
      await expect(button).toBeFocused();
      expect(await button.evaluate(element => getComputedStyle(element).outlineStyle)).toBe('solid');
      await page.keyboard.press('Enter');
      await expect(button).toHaveAttribute('aria-expanded', 'true');
      await expect(page.getByText('原记录没有逐句判定，需核验抽取完整性与拒答处理。')).toBeVisible();
      await expect(page.locator('#verification-detail-1-2')).toContainText('尚未录入人工结论。');
      await expect(page.locator('#verification-detail-1-2 .verification-statements')).toHaveCount(0);
      const detailLayout = await page.locator('#verification-detail-1-2').evaluate(element => ({
        detailWidth: element.scrollWidth,
        viewportWidth: window.innerWidth,
        pageOverflow: document.documentElement.scrollWidth > window.innerWidth,
      }));
      expect(detailLayout.detailWidth).toBeLessThanOrEqual(detailLayout.viewportWidth);
      expect(detailLayout.pageOverflow).toBe(false);
      await page.getByLabel('核验状态', { exact: true }).selectOption('agree');
      await expect(page.getByText('没有符合筛选条件的核验记录。')).toBeVisible();
      for (const name of ['评测总览', '评测工作区', '多模型对比', '幻觉分析', '评测报告']) {
        await nav.getByRole('button', { name, exact: true }).click();
        await expect(page.getByRole('heading', { name, level: 1, exact: true })).toBeVisible();
        await expect(page.getByText('已有评测快照 · 20260930-1958', { exact: true })).toBeVisible();
      }
      await nav.getByRole('button', { name: '评测总览', exact: true }).click();
      await expect(page.getByRole('region', { name: '当前模型质量门禁' })).toContainText('PASS');
      expect(evaluations).toEqual([]);
      await page.getByRole('button', { name: '返回首页' }).click();
      await expect(page.getByRole('heading', { level: 1 })).toContainText('让 RAG 的回答');
    });

    test('live results cannot inherit demo verification and stale notices remain intact', async ({ page }) => {
      let complete!: () => void;
      let count = 0;
      await page.route('**/api/evaluate', async route => {
        count++;
        await new Promise<void>(resolve => { complete = resolve; });
        await route.fulfill({ json: demo.result });
      });
      await page.goto('/#/lab');
      const nav = page.getByRole('navigation', { name: '工作区导航' });
      await nav.getByRole('button', { name: '评测验证', exact: true }).click();
      await expect(page.getByText('尚未加载可核验结果')).toBeVisible();
      await page.getByRole('button', { name: '加载演示', exact: true }).click();
      await nav.getByRole('button', { name: '评测工作区', exact: true }).click();
      await page.getByRole('button', { name: '开始评测', exact: true }).click();
      await expect.poll(() => count).toBe(1);
      await nav.getByRole('button', { name: '评测验证', exact: true }).click();
      await expect(page.getByText('上次结果 · 本次尚未更新')).toBeVisible();
      await expect(page.locator('[data-review-record]')).toHaveCount(13);
      complete();
      await expect(page.getByRole('heading', { name: '评测总览', level: 1, exact: true })).toBeVisible();
      await nav.getByRole('button', { name: '评测验证', exact: true }).click();
      await expect(page.getByText('本次结果尚无对应人工核验材料')).toBeVisible();
      await expect(page.locator('[data-review-record]')).toHaveCount(0);
      await expect(page.getByRole('region', { name: '人工核验范围' })).toHaveCount(0);
      expect(count).toBe(1);
    });
  });
}
