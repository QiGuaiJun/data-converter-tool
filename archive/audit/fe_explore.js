const { chromium } = require('playwright');
const BASE = 'http://127.0.0.1:51978';
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });
  await p.goto(BASE + '/export.html', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1500);
  const info = await p.evaluate(() => {
    const vis = el => !!el && !!(el.offsetWidth || el.offsetHeight);
    const clickable = Array.from(document.querySelectorAll('button, [role=button], .step, .tab, a[href="#"], input[type=radio], input[type=checkbox], select'))
      .filter(vis)
      .map(e => ({ tag: e.tagName, id: e.id || '', cls: (e.className || '').toString().slice(0, 28), txt: (e.textContent || e.value || '').trim().slice(0, 24), type: e.type || '' }));
    const sections = Array.from(document.querySelectorAll('[id]')).filter(vis).map(e => e.id).slice(0, 60);
    return {
      title: document.title,
      bodyText: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 700),
      clickable: clickable.slice(0, 40),
      visibleIds: sections,
    };
  });
  console.log(JSON.stringify(info, null, 2));
  await p.screenshot({ path: __dirname + '/fe_export_initial.png', fullPage: true });
  await b.close();
})();
