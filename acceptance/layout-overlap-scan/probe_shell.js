/** 一次性探针：主壳 / 右栏 / two-col 行 / 按钮 的栅格与宽度来源。 */
const { chromium } = require('playwright');
const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const SIZES = [[1920, 1080], [1600, 900], [1440, 900], [1366, 768], [1280, 720]];
const P = () => {
  const g = (s) => { const e = document.querySelector(s); if (!e) return null; const c = getComputedStyle(e); const r = e.getBoundingClientRect(); return { cols: c.gridTemplateColumns, w: Math.round(r.width), x: Math.round(r.x), right: Math.round(r.right) }; };
  const lab = document.querySelector('#filePrefix').closest('label');
  const labC = getComputedStyle(lab);
  const inp = document.querySelector('#filePrefix');
  const inpC = getComputedStyle(inp);
  return {
    shell: g('.export-shell'), left: g('.export-left'), right: g('.export-right'),
    twoCol: g('.two-col'), prefixLabel: { cols: labC.gridTemplateColumns, w: Math.round(lab.getBoundingClientRect().width), display: labC.display },
    prefixInput: { width: inpC.width, maxW: inpC.maxWidth, minW: inpC.minWidth, boxSizing: inpC.boxSizing, rect: Math.round(inp.getBoundingClientRect().width) },
    remarkParent: g('#exportRemark').parentElement ? (() => { const p = document.querySelector('#exportRemark').parentElement; const c = getComputedStyle(p); return { cls: p.className, display: c.display, cols: c.gridTemplateColumns, rows: c.gridTemplateRows, w: Math.round(p.getBoundingClientRect().width) }; })() : null,
    remark: (() => { const e = document.querySelector('#exportRemark'); const r = e.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.y), bottom: Math.round(r.bottom) }; })(),
    footer: (() => { const e = document.querySelector('.footer-actions'); const r = e.getBoundingClientRect(); const c = getComputedStyle(e); return { w: Math.round(r.width), y: Math.round(r.y), display: c.display, position: c.position, marginTop: c.marginTop, zIndex: c.zIndex, bg: c.backgroundColor }; })(),
    runBtn: (() => { const e = document.querySelector('#runExport'); const r = e.getBoundingClientRect(); return { x: Math.round(r.x), right: Math.round(r.right), inView: r.right <= innerWidth && r.x >= 0 }; })(),
    innerW: innerWidth,
  };
};
(async () => {
  const b = await chromium.launch();
  for (const [w, h] of SIZES) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    await p.goto(`${BASE}/export.html`, { waitUntil: 'networkidle' });
    await p.evaluate(() => { document.querySelector('#newExportTask')?.click(); if (typeof setExportEditorVisible === 'function') setExportEditorVisible(true); });
    await p.waitForSelector('#previewExport', { state: 'visible' });
    const r = await p.evaluate(P);
    console.log(`\n=== ${w}x${h} (innerW ${r.innerW})`);
    console.log('  shell  ', JSON.stringify(r.shell));
    console.log('  left   ', JSON.stringify(r.left));
    console.log('  right  ', JSON.stringify(r.right));
    console.log('  twoCol ', JSON.stringify(r.twoCol), '\n  prefixLabel', JSON.stringify(r.prefixLabel), '\n  prefixInput', JSON.stringify(r.prefixInput));
    console.log('  remark ', JSON.stringify(r.remarkParent), JSON.stringify(r.remark));
    console.log('  footer ', JSON.stringify(r.footer), ' runBtn', JSON.stringify(r.runBtn));
    await p.close();
  }
  await b.close();
})();
