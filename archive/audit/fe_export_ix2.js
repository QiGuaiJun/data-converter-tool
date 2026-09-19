const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:51978';
const log = (...a) => console.log(...a);

async function snap(p, tag) {
  const s = await p.evaluate(() => {
    const vis = el => !!el && !!(el.offsetWidth || el.offsetHeight);
    const ids = Array.from(document.querySelectorAll('[id]')).filter(vis).map(e => e.id);
    const btns = Array.from(document.querySelectorAll('button')).filter(vis).map(e => e.id + ':' + (e.textContent||'').trim().slice(0,16));
    return { ids: ids.slice(0, 70), btns: btns.slice(0, 25) };
  });
  log(`\n[${tag}] 可见 id 数=${s.ids.length}`);
  log('  ids:', s.ids.join(', '));
  log('  btns:', s.btns.join(' | '));
  return s;
}

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = [];
  p.on('pageerror', e => errs.push('pageerror: ' + e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 160)); });

  await p.goto(BASE + '/export.html', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1200);
  await snap(p, '初始');

  await p.click('#newExportTask');
  await p.waitForTimeout(2000);
  await snap(p, '点新增导出后');

  // 尝试选连接
  const sel = await p.$('#exportConnection');
  if (sel && await sel.isVisible()) {
    const opts = await p.$$eval('#exportConnection option', els => els.map(e => e.value).filter(Boolean));
    log('  连接选项:', opts);
    if (opts.length) { await p.selectOption('#exportConnection', opts[0]); await p.waitForTimeout(2000); }
    await snap(p, '选连接后');
  } else {
    log('  #exportConnection 不可见，尝试点其他入口');
  }

  // 寻找「查询」数据源入口
  for (const id of ['#sourceTypeQuery', '#querySource', '#tabQuery', '#addQuery', '#useQuery']) {
    const el = await p.$(id);
    if (el && await el.isVisible()) { await el.click(); await p.waitForTimeout(800); log('  点击了', id); }
  }
  const qArea = await p.$('#exportSql');
  if (qArea && await qArea.isVisible()) {
    await qArea.fill('select 1 as a;\nselect 2 as b, 3 as c');
    log('  已填 #exportSql');
  } else { log('  #exportSql 仍不可见'); }

  // 多查询模式
  const mq = await p.$('#multiQuery');
  if (mq && await mq.isVisible()) { await mq.click(); await p.waitForTimeout(800); log('  点击 #multiQuery'); }
  await snap(p, '准备预览');

  const pv = await p.$('#previewExport');
  if (pv && await pv.isVisible()) {
    await pv.click();
    log('  已点预览');
  } else { log('  #previewExport 不可见，尝试其他预览按钮'); }
  await p.waitForTimeout(4500);

  const after = await p.evaluate(() => {
    const el = document.querySelector('#exportPreviewTable');
    return {
      blockCount: document.querySelectorAll('#exportPreviewTable .preview-block').length,
      tableCount: el ? el.querySelectorAll('table').length : 0,
      previewText: el ? (el.innerText || '').replace(/\s+/g, ' ').slice(0, 400) : '(无容器)',
      metaText: (document.querySelector('#exportPreviewMeta')?.innerText || '').slice(0, 150),
      staleHint: document.body.innerText.includes('仅预览第 1 条'),
    };
  });
  log('\n--- 预览结果 ---');
  log(JSON.stringify(after, null, 2));
  log('\n前端错误:', errs.length ? '\n' + errs.join('\n') : '无');
  await p.screenshot({ path: __dirname + '/fe_export_ix2.png', fullPage: true });
  await b.close();
})();
