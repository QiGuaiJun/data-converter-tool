const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 1400 } });
  await p.goto('http://127.0.0.1:51978/jobs.html', { waitUntil: 'networkidle' });
  await p.waitForTimeout(2000);
  const r = await p.evaluate(() => {
    const items = [...document.querySelectorAll('.log-item')];
    const boxes = items.map((e) => { const b = e.getBoundingClientRect(); const body = e.querySelector('.log-body'); const bb = body ? body.getBoundingClientRect() : null;
      return { item: [Math.round(b.y), Math.round(b.height)], body: bb ? [Math.round(bb.y), Math.round(bb.height)] : null, ovf: body ? getComputedStyle(e).overflow : null, cls: e.className }; });
    let overlaps = 0, pairs = [];
    for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
      const a = items[i].getBoundingClientRect(), c = items[j].getBoundingClientRect();
      const oy = Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top);
      if (oy > 2) { overlaps++; if (pairs.length < 5) pairs.push([i, j, Math.round(oy)]); }
    }
    return { count: items.length, boxes: boxes.slice(0, 8), overlaps, pairs, docH: document.documentElement.scrollHeight };
  });
  console.log(JSON.stringify(r, null, 2));
  await p.evaluate(() => { const l = [...document.querySelectorAll('.log-item')]; if (l[0]) l[0].scrollIntoView({ block: 'start' }); });
  await p.waitForTimeout(300);
  await p.screenshot({ path: path.join(__dirname, 'shots', 'other-jobs-settled.png') });
  await b.close();
})();
