import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

for (const viewport of [{width:1920,height:1080},{width:1280,height:800}]) {
  test(`landing layout and evidence remain usable at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const api: string[] = [];
    page.on('request', request => { if (request.url().includes('/api/')) api.push(request.url()); });
    await page.goto('/');
    await expect(page.getByRole('heading', {level:1})).toContainText('都能逐句查到出处。');
    await expect(page.getByRole('list', {name:'证据验证流程'}).locator('li')).toHaveCount(5);
    await expect(page.locator('.landing-proof img')).toHaveCount(0);
    await expect(page.getByRole('region', {name:'DeepSeek-V4.1-Flash 演示快照质量门禁'})).toBeAttached();
    await expect(page.getByRole('region', {name:'DeepSeek-V4.1-Flash 演示快照逐句判定'})).toBeAttached();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(api).toEqual([]);
    const resources = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => entry.name));
    expect(resources.some(url => /Charts\.tsx|echarts|fixtures\/demo/.test(url))).toBe(false);
    expect(resources.some(url => /landing\/(quality-gate|evidence-trace).*\.png/.test(url))).toBe(false);
    if (viewport.width >= 1280) {
      expect((await page.getByRole('link',{name:'查看演示'}).first().boundingBox())!.y).toBeLessThan(viewport.height);
      expect((await page.locator('.evidence-flow').getByText('质量门禁',{exact:true}).boundingBox())!.y).toBeLessThan(viewport.height);
    }
    const presentation = await page.evaluate(() => {
      const hero = document.querySelector('h1')!;
      const heroStyle = getComputedStyle(hero);
      return {
        heroSize: heroStyle.fontSize,
        heroWeight: heroStyle.fontWeight,
        heroLineHeight: parseFloat(heroStyle.lineHeight),
        heroHeight: hero.getBoundingClientRect().height,
        keyword: getComputedStyle(document.querySelector('h1 span')!).backgroundImage,
        keywordAnimation: getComputedStyle(document.querySelector('h1 span')!).animationName,
        sectionTitleSize: getComputedStyle(document.querySelector('.landing-section-heading h2')!).fontSize,
        flowGap: getComputedStyle(document.querySelector('.evidence-steps')!).rowGap,
        rowMinHeights: [...document.querySelectorAll('.evidence-step-content')].map(node => getComputedStyle(node).minHeight),
        flowBottom: document.querySelector('.evidence-flow')!.getBoundingClientRect().bottom,
        descriptions: [...document.querySelectorAll('.evidence-step-detail')].map(node => ({ height: node.getBoundingClientRect().height, lineHeight: parseFloat(getComputedStyle(node).lineHeight) })),
        offsets: [...document.querySelectorAll('.evidence-node')].map(node => node.getBoundingClientRect().x),
        source: document.querySelector('.landing-proof figcaption')!.textContent,
      };
    });
    expect(presentation.heroSize).toBe(viewport.width === 1920 ? '64px' : '54px');
    expect(presentation.heroWeight).toBe('800');
    expect(presentation.heroLineHeight / parseFloat(presentation.heroSize)).toBeCloseTo(1.16, 2);
    expect(presentation.heroHeight).toBeLessThanOrEqual(presentation.heroLineHeight * 2 + 1);
    expect(presentation.heroHeight).toBeGreaterThanOrEqual(presentation.heroLineHeight * 1.9);
    expect(presentation.keyword).toContain('linear-gradient');
    expect(presentation.keywordAnimation).toBe('none');
    expect(presentation.sectionTitleSize).toBe(viewport.width === 1920 ? '36px' : '32px');
    expect(presentation.flowGap).toBe('28px');
    for (const height of presentation.rowMinHeights) expect(height).toBe(viewport.width === 1920 ? '72px' : '64px');
    expect(presentation.flowBottom).toBeLessThanOrEqual(viewport.height + 1);
    for (const description of presentation.descriptions) expect(description.height).toBeLessThanOrEqual(description.lineHeight + 1);
    expect(presentation.offsets[1]).toBeGreaterThan(presentation.offsets[0]);
    expect(presentation.offsets[2]).toBeGreaterThan(presentation.offsets[1]);
    expect(presentation.source).toContain('非本次实时评测');
    const gatePreview = page.getByRole('region', {name:'DeepSeek-V4.1-Flash 演示快照质量门禁'});
    const evidencePreview = page.getByRole('region', {name:'DeepSeek-V4.1-Flash 演示快照逐句判定'});
    await expect(gatePreview.getByText('PASS', {exact:true})).toBeAttached();
    await expect(gatePreview.getByText('3 条正向用例', {exact:true})).toBeAttached();
    await expect(gatePreview.locator('.gate-metrics dd > span')).toHaveText(['100.0%', '100.0%']);
    await expect(gatePreview.locator('.gate-metrics small')).toHaveText(['门槛 ≥ 80.0%', '门槛 ≥ 80.0%']);
    await expect(evidencePreview.locator('.trace-statement-text')).toHaveText('星云科技最新一轮B轮融资金额达5亿元人民币。');
    await expect(evidencePreview.locator('.trace-evidence blockquote')).toHaveText('星云科技已累计获得三轮融资，最新一轮B轮融资金额达5亿元人民币。');
    await expect(evidencePreview.getByText('有依据', {exact:true})).toBeAttached();
    const clarity = await page.locator('.landing-product-preview').evaluateAll(previews => previews.map(preview => {
      const ancestors = [];
      for (let node: Element | null = preview; node; node = node.parentElement) {
        const css = getComputedStyle(node);
        ancestors.push({transform:css.transform,filter:css.filter,backdrop:css.backdropFilter,opacity:css.opacity});
      }
      const fonts = [...preview.querySelectorAll('.gate-context dt,.gate-metrics small,.trace-statement-heading,.trace-evidence-label')].map(node => parseFloat(getComputedStyle(node).fontSize));
      const textSizes = [...preview.querySelectorAll('.trace-statement-text,.trace-evidence blockquote,.gate-metrics dd > span')].map(node => ({
        className:node.className,
        quote:node.tagName === 'BLOCKQUOTE',
        metric:node.matches('.gate-metrics dd > span'),
        size:parseFloat(getComputedStyle(node).fontSize),
      }));
      return {ancestors,fonts,textSizes,overflow:preview.scrollWidth>preview.clientWidth+1,interactive:preview.querySelectorAll('button,a,input,select,textarea,summary,details').length};
    }));
    expect(clarity).toHaveLength(2);
    for (const preview of clarity) {
      expect(preview.overflow).toBe(false);
      expect(preview.interactive).toBe(0);
      for (const ancestor of preview.ancestors) expect(ancestor).toEqual({transform:'none',filter:'none',backdrop:'none',opacity:'1'});
      expect(preview.fonts.length).toBeGreaterThan(0);
      for (const size of preview.fonts) expect(size).toBeGreaterThanOrEqual(13);
      for (const text of preview.textSizes) expect(text.size).toBe(text.metric ? 24 : text.quote ? 18 : 20);
    }
    await page.emulateMedia({reducedMotion:'reduce'});
    await expect(page.locator('.evidence-flow')).toHaveAttribute('data-complete', 'true');
    await expect.poll(() => page.locator('.evidence-pulse').first().evaluate(el => getComputedStyle(el).animationName)).toBe('none');
    for (const selector of ['.landing-hero-copy', '.evidence-flow']) {
      await expect.poll(() => page.locator(selector).evaluate(el => getComputedStyle(el).animationName)).toBe('none');
    }
    await page.screenshot({path:`test-results/landing-${viewport.width}x${viewport.height}.png`,fullPage:true});
  });
}

test('product previews retain functional links, restrained hover and visible keyboard focus', async ({ page }) => {
  await page.goto('/');
  const preview = page.getByRole('link', {name:'进入演示查看质量门禁'});
  await expect(preview).toHaveAttribute('href', '#/lab/demo');
  await preview.hover();
  await expect.poll(() => preview.evaluate(el => getComputedStyle(el).borderColor)).not.toBe('rgba(255, 255, 255, 0.16)');
  await page.mouse.move(0, 0);
  await preview.focus();
  const focus = await preview.evaluate(el => ({ outline: getComputedStyle(el).outlineWidth, style: getComputedStyle(el).outlineStyle, offset: getComputedStyle(el).outlineOffset }));
  expect(focus).toEqual({outline:'2px',style:'solid',offset:'6px'});
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/#\/lab\/demo$/);
  await expect(page.locator('.app-shell .gate-value')).toHaveText('PASS');
});

test('demo works without backend and survives home/back navigation', async ({ page }) => {
  const posts: string[] = [];
  await page.route(/\/api\/(?:health|evaluate)(?:\?|$)/, route => {
    if (route.request().method() === 'POST') posts.push(route.request().url());
    return route.fulfill({status:503,body:'Service unavailable'});
  });
  await page.goto('/');
  await page.getByRole('link',{name:'查看演示'}).first().click();
  await expect(page.getByText('PASS',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'多模型对比',exact:true}).click();
  await expect(page.getByRole('table', { name: /各模型的全部原始指标/ })).toBeVisible();
  await page.getByRole('button',{name:'返回首页',exact:true}).click();
  await expect(page.getByRole('heading',{level:1})).toContainText('都能逐句查到出处。');
  await page.goBack();
  await expect(page.getByRole('table', { name: /各模型的全部原始指标/ })).toBeVisible();
  await page.getByRole('button',{name:'返回首页',exact:true}).click();
  await page.getByRole('link',{name:'进入工作台'}).click();
  await expect(page.getByRole('table', { name: /各模型的全部原始指标/ })).toBeVisible();
  expect(posts).toEqual([]);
});

test('direct entries, refresh, keyboard focus and configuration retain existing behavior', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({json:{status:'ok'}}));
  await page.goto('/#/lab/demo');
  await expect(page.getByText('PASS',{exact:true})).toBeVisible();
  await page.reload();
  await expect(page.getByText('PASS',{exact:true})).toBeVisible();
  await page.goto('/#/lab/workspace');
  await expect(page.getByRole('heading',{name:'评测工作区',exact:true})).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading',{name:'评测工作区',exact:true})).toBeVisible();
  await page.goto('/');
  await page.reload();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link',{name:'跳到主要内容'})).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#landing-content')).toBeFocused();
  await page.getByRole('link',{name:'配置评测'}).first().click();
  await expect(page.getByRole('heading',{name:'评测工作区',exact:true})).toBeVisible();
});

test('Chinese flow plays exactly twice with no manual controls and retains the final state', async ({ page }) => {
  await page.goto('/');
  const flow = page.locator('.evidence-flow');
  await expect(flow).toHaveAttribute('data-cycle', '1');
  await expect(flow.locator('button')).toHaveCount(0);
  await expect(page.locator('.landing-motion-toggle,.evidence-replay,#evidence-inspector')).toHaveCount(0);
  for (const name of ['Question','Retrieval Context','Answer','Evidence Check','Quality Gate']) await expect(page.getByText(name,{exact:true})).toHaveCount(0);
  await expect(flow).toHaveAttribute('data-cycle', '2');
  await expect(flow).toHaveAttribute('data-complete', 'true');
  await expect(flow.locator('li[aria-current="step"]')).toContainText('质量门禁');
  await expect(page.locator('.evidence-pulse.is-travelling')).toHaveCount(0);
  await page.waitForTimeout(2600);
  await expect(flow).toHaveAttribute('data-cycle', '2');
  await expect(flow).toHaveAttribute('data-playing', 'false');
});

test('flow pauses offscreen while the background continues across the lower page', async ({ page }) => {
  await page.goto('/');
  const flow = page.locator('.evidence-flow');
  const canvas = page.locator('.research-field canvas');
  await page.waitForTimeout(600);
  await page.locator('.landing-footer').scrollIntoViewIfNeeded();
  await expect(flow).toHaveAttribute('data-paused', 'true');
  const current = await flow.locator('li[aria-current="step"]').textContent();
  await expect(canvas).toHaveAttribute('data-state', 'running');
  const first = await canvas.evaluate(el => (el as HTMLCanvasElement).toDataURL());
  await expect.poll(() => canvas.evaluate((el, baseline) => (el as HTMLCanvasElement).toDataURL() !== baseline, first)).toBe(true);
  expect(await page.locator('.research-field').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThan(await page.locator('.landing-hero').evaluate(el => el.getBoundingClientRect().height));
  await page.waitForTimeout(2600);
  expect(await flow.locator('li[aria-current="step"]').textContent()).toBe(current);
  await page.evaluate(() => window.scrollTo({top:0,behavior:'instant'}));
  await expect(flow).toHaveAttribute('data-paused', 'false');
  await expect(flow).toHaveAttribute('data-complete', 'true', {timeout:6000});
  await expect(flow).toHaveAttribute('data-cycle', '2');
  await expect(canvas).toHaveAttribute('data-state', 'running');
});

test('visibility pauses flow and background without resetting playback', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(650);
  const flow = page.locator('.evidence-flow');
  const canvas = page.locator('.research-field canvas');
  await page.evaluate(() => { Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange')); });
  await expect(flow).toHaveAttribute('data-paused','true');
  await expect(canvas).toHaveAttribute('data-state','paused');
  const frames = await canvas.getAttribute('data-frames');
  await page.waitForTimeout(600);
  expect(await canvas.getAttribute('data-frames')).toBe(frames);
  await page.evaluate(() => { Reflect.deleteProperty(document,'hidden');document.dispatchEvent(new Event('visibilitychange')); });
  await expect(canvas).toHaveAttribute('data-state','running');
  await expect(flow).toHaveAttribute('data-complete','true', {timeout:6000});
  await expect(flow).toHaveAttribute('data-cycle','2');
});

test('reduced motion shows the final state and preference recovery cannot replay the flow', async ({ page }) => {
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.goto('/');
  const flow = page.locator('.evidence-flow');
  const canvas = page.locator('.research-field canvas');
  await expect(flow).toHaveAttribute('data-complete','true');
  await expect(flow.locator('li[aria-current="step"]')).toContainText('质量门禁');
  await expect(canvas).toHaveAttribute('data-state','paused');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await expect(canvas).toHaveAttribute('data-state','running');
  await page.waitForTimeout(1000);
  await expect(flow).toHaveAttribute('data-playing','false');
  await page.reload();
  await expect(flow).toHaveAttribute('data-complete','false');
  await page.waitForTimeout(650);
  await page.emulateMedia({reducedMotion:'reduce'});
  await expect(flow).toHaveAttribute('data-complete','true');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await expect(flow).toHaveAttribute('data-playing','false');
});

test('lower evidence motion starts on view, pauses offscreen and hidden, and honors reduced motion', async ({ page }) => {
  await page.goto('/');
  const journey = page.locator('.evidence-journey');
  await expect(journey).toHaveAttribute('data-started', 'false');
  await journey.scrollIntoViewIfNeeded();
  await expect(journey).toHaveAttribute('data-playing', 'true');
  const pulse = journey.locator('.journey-pulse-0');
  await expect(pulse).toHaveAttribute('d', / C /);
  await expect.poll(() => pulse.evaluate(el => getComputedStyle(el).animationIterationCount)).toBe('2');
  const offset = await pulse.evaluate(el => getComputedStyle(el).strokeDashoffset);
  await expect.poll(() => pulse.evaluate(el => getComputedStyle(el).strokeDashoffset)).not.toBe(offset);
  await page.locator('.landing-footer').scrollIntoViewIfNeeded();
  await expect(journey).toHaveAttribute('data-playing', 'false');
  await expect.poll(() => pulse.evaluate(el => getComputedStyle(el).animationPlayState)).toBe('paused');
  const pausedTime = await pulse.evaluate(el => el.getAnimations()[0]?.currentTime);
  await page.waitForTimeout(800);
  expect(await pulse.evaluate(el => el.getAnimations()[0]?.currentTime)).toBe(pausedTime);
  await expect(journey).toHaveAttribute('data-complete', 'false');
  await journey.scrollIntoViewIfNeeded();
  await expect(journey).toHaveAttribute('data-playing', 'true');
  await page.evaluate(() => { Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange')); });
  await expect(journey).toHaveAttribute('data-playing','false');
  await page.evaluate(() => { Reflect.deleteProperty(document,'hidden');document.dispatchEvent(new Event('visibilitychange')); });
  await expect(journey).toHaveAttribute('data-playing','true');
  await page.emulateMedia({reducedMotion:'reduce'});
  await expect(journey).toHaveAttribute('data-complete','true');
  await expect.poll(() => pulse.evaluate(el => getComputedStyle(el).animationName)).toBe('none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await expect(journey).toHaveAttribute('data-playing','false');
});

test('lower evidence illustration completes its two finite passes and does not replay on scroll', async ({ page }) => {
  await page.goto('/');
  const journey = page.locator('.evidence-journey');
  await journey.scrollIntoViewIfNeeded();
  await expect(journey).toHaveAttribute('data-playing','true');
  await expect(journey).toHaveAttribute('data-complete','true',{timeout:6500});
  await expect(journey).toHaveAttribute('data-playing','false');
  await page.evaluate(() => window.scrollTo({top:0,behavior:'instant'}));
  await journey.scrollIntoViewIfNeeded();
  await expect(journey).toHaveAttribute('data-complete','true');
  await expect(page.locator('.research-field canvas')).toHaveAttribute('data-state','running');
});
