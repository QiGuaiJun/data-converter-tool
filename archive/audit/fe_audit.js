// data-converter-tool 前端真实渲染审计
const { chromium } = require('playwright');

const BASE = 'http://127.0.0.1:51978';
const PAGES = [
  ['/', '导入'],
  ['/export.html', '导出'],
  ['/connections.html', '连接'],
  ['/query.html', '查询'],
  ['/tables.html', '表'],
  ['/jobs.html', '作业'],
  ['/schedule.html', '定时任务'],
  ['/sync.html', '同步'],
  ['/api.html', 'API'],
  ['/feedback.html', '反馈'],
  ['/docs.html', '操作手册'],
];

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const report = [];
  let totalErr = 0, totalFail = 0;

  for (const [path, label] of PAGES) {
    const page = await ctx.newPage();
    const pageErrors = [], consoleErrors = [], failedReqs = [], badResponses = [];
    page.on('pageerror', e => pageErrors.push(String(e.message || e).split('\n')[0]));
    page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
    page.on('requestfailed', r => failedReqs.push(`${r.url().replace(BASE, '')} :: ${r.failure()?.errorText}`));
    page.on('response', r => {
      if (r.status() >= 400) badResponses.push(`${r.url().replace(BASE, '')} -> ${r.status()}`);
    });

    try {
      await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 25000 });
      await page.waitForTimeout(1200);
    } catch (e) {
      pageErrors.push('NAV_FAIL: ' + String(e.message).split('\n')[0]);
    }

    // 关键 DOM 检查
    const dom = await page.evaluate(() => {
      const ribbon = document.querySelectorAll('.ribbon-item, .app-ribbon a').length;
      const body = document.body;
      const text = (body.innerText || '').trim();
      return {
        ribbonCount: ribbon,
        bodyTextLen: text.length,
        bodyTextHead: text.slice(0, 90).replace(/\s+/g, ' '),
        scripts: document.scripts.length,
        stylesheets: document.styleSheets.length,
        emptySheets: Array.from(document.styleSheets).filter(s => {
          try { return s.cssRules && s.cssRules.length === 0; } catch { return false; }
        }).length,
        themeLoaded: Array.from(document.styleSheets).some(s => (s.href || '').includes('theme.css')),
        title: document.title,
      };
    });

    totalErr += pageErrors.length;
    totalFail += badResponses.length + failedReqs.length;

    report.push({ path, label, pageErrors, consoleErrors, failedReqs, badResponses, dom });
    console.log('='.repeat(70));
    console.log(`${label.padEnd(6)} ${path}`);
    console.log(`  title="${dom.title}" ribbon=${dom.ribbonCount} 可见文本=${dom.bodyTextLen}字 样式表=${dom.stylesheets}(空=${dom.emptySheets}) theme.css加载=${dom.themeLoaded}`);
    console.log(`  文本开头: ${dom.bodyTextHead}`);
    if (pageErrors.length) console.log('  ❌ pageerror: ' + pageErrors.join(' || '));
    if (consoleErrors.length) console.log('  ❌ console.error: ' + consoleErrors.join(' || '));
    if (failedReqs.length) console.log('  ❌ 请求失败: ' + failedReqs.join(' || '));
    if (badResponses.length) console.log('  ❌ 4xx/5xx: ' + badResponses.join(' || '));
    if (!pageErrors.length && !consoleErrors.length && !failedReqs.length && !badResponses.length)
      console.log('  ✅ 无任何前端错误');

    await page.close();
  }

  console.log('='.repeat(70));
  console.log(`汇总: 页面 ${PAGES.length} 个 | pageerror ${totalErr} | 失败请求/4xx-5xx ${totalFail}`);
  require('fs').writeFileSync(__dirname + '/fe_report.json', JSON.stringify(report, null, 2));
  await browser.close();
})();
