// 导出页「多查询预览」真实交互验证（M12-001 的 UI 侧）
const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:51978';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 150)); });

  await page.goto(BASE + '/export.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);

  // 探索初始状态
  const init = await page.evaluate(() => {
    const q = s => document.querySelector(s);
    const vis = el => !!el && !!(el.offsetWidth || el.offsetHeight);
    return {
      connOptions: Array.from(document.querySelectorAll('#exportConnection option')).map(o => o.value + '|' + o.textContent.trim()),
      exportSqlVisible: vis(q('#exportSql')),
      multiQueryVisible: vis(q('#multiQuery')),
      singleQueryVisible: vis(q('#singleQuery')),
      previewBtnVisible: vis(q('#previewExport')),
      sourceListVisible: vis(q('#exportSourceList')),
    };
  });
  console.log('--- 初始状态 ---');
  console.log(JSON.stringify(init, null, 2));

  // 选连接
  const cid = init.connOptions.find(o => o.split('|')[0])?.split('|')[0];
  if (cid) {
    await page.selectOption('#exportConnection', cid);
    await page.waitForTimeout(1500);
  }
  console.log('已选连接:', cid);

  // 切到「多查询」并填 SQL
  try { await page.click('#multiQuery', { timeout: 5000 }); console.log('已点击 多查询'); } catch (e) { console.log('多查询点击失败:', e.message.slice(0, 80)); }
  await page.waitForTimeout(600);

  // 尝试用 #exportSql 填 SQL；若不可见，尝试用 queryName + 别的方式
  let filled = false;
  for (const sel of ['#exportSql', '#exportSqlInput', 'textarea[name=exportSql]']) {
    const el = await page.$(sel);
    if (el && await el.isVisible()) {
      await el.fill('select 1 as a; select 2 as b, 3 as c');
      filled = true;
      console.log('已填充', sel);
      break;
    }
  }
  if (!filled) {
    // 多查询模式可能用「添加查询」列表
    const btns = await page.$$eval('button, a', els => els.map(e => e.id + '|' + e.textContent.trim()).filter(x => x.includes('添加') || x.includes('查询')));
    console.log('可能相关按钮:', btns.slice(0, 12));
  }

  // 点预览
  try {
    await page.click('#previewExport', { timeout: 5000 });
    console.log('已点击 预览');
  } catch (e) { console.log('预览点击失败:', e.message.slice(0, 80)); }
  await page.waitForTimeout(4000);

  const after = await page.evaluate(() => {
    const el = document.querySelector('#exportPreviewTable');
    const meta = document.querySelector('#exportPreviewMeta');
    return {
      previewTableExists: !!el,
      previewBlockCount: document.querySelectorAll('#exportPreviewTable .preview-block').length,
      innerTableCount: el ? el.querySelectorAll('table').length : 0,
      previewText: el ? (el.innerText || '').replace(/\s+/g, ' ').slice(0, 500) : '',
      metaText: meta ? (meta.innerText || '').slice(0, 200) : '',
      hasStaleHint: document.body.innerText.includes('仅预览第 1 条'),
      statusText: (document.querySelector('#exportStatus')?.innerText || '').slice(0, 200),
    };
  });
  console.log('--- 预览后 ---');
  console.log(JSON.stringify(after, null, 2));
  console.log('--- 前端错误 ---');
  console.log(errs.length ? errs.join('\n') : '无');

  await page.screenshot({ path: __dirname + '/fe_export_multiquery.png', fullPage: true });
  console.log('截图: fe_export_multiquery.png');
  await browser.close();
})();
