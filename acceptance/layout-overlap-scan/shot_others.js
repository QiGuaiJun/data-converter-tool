const { chromium } = require('playwright');
const path = require('path');
const T = [
  ['query', '/query.html', '#querySql', 900, 300],
  ['api', '/api.html', '.module-status', 260, 220],
  ['docs', '/docs.html', '.thm-table', 300, 220],
  ['jobs', '/jobs.html', '.log-body', 900, 400],
  ['tables', '/tables.html', '.table-detail-view', 900, 400],
];
(async () => {
  const b = await chromium.launch();
  for (const [name, url, sel, cw, ch] of T) {
    const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
    await p.goto('http://127.0.0.1:51978' + url, { waitUntil: 'networkidle' });
    await p.waitForTimeout(900);
    const info = await p.evaluate((sel) => {
      const e = document.querySelector(sel);
      if (!e) return null;
      e.scrollIntoView({ block: 'center' });
      const r = e.getBoundingClientRect();
      const c = getComputedStyle(e);
      return { rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], pos: c.position, z: c.zIndex, ovf: c.overflow, cls: e.className, parent: e.parentElement ? e.parentElement.className : null, text: (e.textContent || '').replace(/\s+/g, ' ').slice(0, 80) };
    }, sel);
    await p.waitForTimeout(300);
    const r = info ? info.rect : [0, 0, cw, ch];
    const clip = { x: Math.max(0, Math.min(r[0] - 40, 1440 - cw)), y: Math.max(0, Math.min(r[1] - 40, 900 - ch)), width: cw, height: ch };
    await p.screenshot({ path: path.join(__dirname, 'shots', `other-${name}.png`), clip });
    console.log(name, JSON.stringify(info));
    await p.close();
  }
  await b.close();
})();
