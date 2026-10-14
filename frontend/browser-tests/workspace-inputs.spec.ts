import { test, expect } from '@playwright/test';

for (const viewport of [{ width: 1920, height: 1080 }, { width: 1280, height: 800 }]) {
  test(`example and computer inputs are usable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    const evaluations: string[] = [];
    const imports: { kind: string | null; filename: string | null; body: Buffer }[] = [];
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('http://127.0.0.1:5173/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/api/health') return route.fulfill({ json: { status: 'ok' } });
      if (url.pathname === '/api/inputs') {
        const kind = url.searchParams.get('kind');
        const filename = url.searchParams.get('filename');
        const body = request.postDataBuffer()!;
        imports.push({ kind, filename, body });
        const suffix = kind === 'document' ? 'txt' : 'csv';
        return route.fulfill({ json: { kind, file_name: filename, relative_path: `work/uploads/${'a'.repeat(32)}.${suffix}`, size_bytes: body.length } });
      }
      evaluations.push(request.url());
      return route.fulfill({ status: 500, json: { detail: 'Browser verification never runs models' } });
    });
    await page.setViewportSize(viewport);
    await page.goto('/#/lab/workspace');
    await expect(page.getByRole('heading', { name: '评测配置' })).toBeVisible();
    await expect(page.getByRole('button', { name: '示例资料', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('radiogroup', { name: '示例评测资料' }).getByRole('radio')).toHaveCount(3);
    await expect(page.locator('#doc-path')).not.toBeVisible();
    await page.getByRole('radio', { name: /存款保险/ }).focus();
    await page.keyboard.press('Space');
    await expect(page.getByRole('radio', { name: /存款保险/ })).toBeChecked();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await page.screenshot({ path: `test-results/workspace-inputs-examples-${viewport.width}x${viewport.height}.png` });

    await page.getByRole('button', { name: '电脑文件', exact: true }).click();
    await expect(page.getByRole('button', { name: '开始评测', exact: true })).toBeDisabled();
    const documentBytes = Buffer.from('用于文件导入验收的本地资料。\n只检查导入，不运行模型。', 'utf8');
    await page.getByLabel('从电脑选择评测资料').setInputFiles({ name: '导入验收资料.txt', mimeType: 'text/plain', buffer: documentBytes });
    await expect(page.getByRole('status').filter({ hasText: '已导入评测服务' })).toContainText('导入验收资料.txt');
    await expect(page.getByRole('button', { name: '开始评测', exact: true })).toBeEnabled();
    expect(imports[0]).toEqual({ kind: 'document', filename: '导入验收资料.txt', body: documentBytes });
    await page.getByLabel('测试集来源').selectOption('custom');
    const csv = Buffer.from('question,ground_truth\n示例问题,示例参考答案\n', 'utf8');
    await page.getByLabel('从电脑选择测试集').setInputFiles({ name: '我的测试集.csv', mimeType: 'text/csv', buffer: csv });
    await expect(page.getByRole('status').filter({ hasText: '我的测试集.csv' })).toContainText('已导入评测服务');
    expect(imports[1]).toEqual({ kind: 'testset', filename: '我的测试集.csv', body: csv });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    await page.screenshot({ path: `test-results/workspace-inputs-files-${viewport.width}x${viewport.height}.png` });
    await page.getByRole('button', { name: '示例资料', exact: true }).click();
    await expect(page.getByRole('radio', { name: /存款保险/ })).toBeChecked();
    await page.getByRole('button', { name: '电脑文件', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '导入验收资料.txt' })).toBeVisible();
    expect(imports).toHaveLength(2);
    expect(evaluations).toEqual([]);
    expect(errors).toEqual([]);
  });
}
