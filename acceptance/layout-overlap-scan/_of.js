const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await p.goto('http://127.0.0.1:51978/export.html', { waitUntil: 'networkidle' });
  await p.evaluate(() => { document.querySelector('#newExportTask')?.click(); if (typeof setExportEditorVisible === 'function') setExportEditorVisible(true); });
  await p.waitForSelector('#previewExport', { state: 'visible' });
  console.log(JSON.stringify(await p.evaluate(() => {
    const f = document.querySelector('.footer-actions');
    const chain = [];
    let e = f;
    while (e && e !== document.documentElement) { const c = getComputedStyle(e); if (c.position !== 'static') chain.push((e.className || e.tagName) + ' pos=' + c.position); e = e.parentElement; }
    const op = f.offsetParent;
    const panel = document.querySelector('.tab-panel.active');
    return { op: op ? op.className || op.tagName : null, positionedAncestors: chain,
             panelCls: panel.className, panelRect: panel.getBoundingClientRect().toJSON(),
             panelPadBottom: getComputedStyle(panel).paddingBottom,
             remarkBottom: document.querySelector('#exportRemark').getBoundingClientRect().bottom,
             footerTop: f.getBoundingClientRect().top, footerBottom: f.getBoundingClientRect().bottom };
  }), null, 2));
  await b.close();
})();
