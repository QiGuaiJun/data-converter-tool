/**
 * 诊断：导出页/导入页元素为何不可交互。
 */
const { chromium } = require('playwright');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:51979';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  const errs = [];
  page.on('pageerror', (e) => errs.push(String(e.message).slice(0, 300)));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('CONSOLE:' + m.text().slice(0, 200)); });

  for (const [url, ids] of [['/export.html', ['#exportSql', '#singleQuery', '#outputName', '#exportTaskName', '#previewExport', '#runExport']],
                            ['/index.html', ['#tableName', '#previewButton', '#fileInput']]]) {
    await page.goto(BASE + url, { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    console.log(`\n===== ${url} =====`);
    for (const sel of ids) {
      const info = await page.evaluate((s) => {
        const el = document.querySelector(s);
        if (!el) return { exists: false };
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        // 谁挡在上面？
        let top = null;
        if (r.width && r.height) {
          const hit = document.elementFromPoint(r.left + Math.min(r.width / 2, 20), r.top + Math.min(r.height / 2, 10));
          if (hit && hit !== el && !el.contains(hit)) top = hit.tagName + (hit.id ? '#' + hit.id : '') + (hit.className ? '.' + String(hit.className).split(' ')[0] : '');
        }
        return {
          exists: true, display: cs.display, visibility: cs.visibility, opacity: cs.opacity,
          pointerEvents: cs.pointerEvents, disabled: el.disabled === true,
          rect: { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) },
          offsetParent: el.offsetParent ? (el.offsetParent.tagName + (el.offsetParent.id ? '#' + el.offsetParent.id : '')) : null,
          coveredBy: top,
        };
      }, sel);
      console.log(sel, JSON.stringify(info));
    }
    // 页面是否有遮罩层
    const overlay = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll('div,section').forEach((el) => {
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        if ((cs.position === 'fixed' || cs.position === 'absolute') && r.width > 600 && r.height > 400 && cs.display !== 'none' && cs.visibility !== 'hidden') {
          out.push({ tag: el.tagName, id: el.id, cls: String(el.className).slice(0, 40), z: cs.zIndex, op: cs.opacity, pe: cs.pointerEvents, w: Math.round(r.width), h: Math.round(r.height) });
        }
      });
      return out;
    });
    console.log('可疑大浮层:', JSON.stringify(overlay));
    await page.screenshot({ path: path.resolve(__dirname, `evidence/20260919/screenshots/diag-${url.replace(/\W/g, '')}.png`) });
  }
  console.log('\npageerrors:', errs.slice(0, 6));
  await browser.close();
})();
