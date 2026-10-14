const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path');
const SQL = fs.readFileSync(path.join(__dirname, '..', 'replay-export-preview-error', 'preview_sample.sql'), 'utf8');
(async () => {
  const b = await chromium.launch();
  for (const [w, h] of [[1440, 900], [1366, 768]]) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    await p.goto('http://127.0.0.1:51978/export.html', { waitUntil: 'networkidle' });
    await p.evaluate(() => { document.querySelector('#newExportTask')?.click(); if (typeof setExportEditorVisible === 'function') setExportEditorVisible(true); });
    await p.waitForSelector('#previewExport', { state: 'visible' });
    await p.waitForFunction(() => (document.querySelector('#exportConnection')?.options.length || 0) > 1);
    await p.evaluate(({ sql }) => {
      const s = document.querySelector('#exportConnection'); s.value = '0f22c4dfd8834709807341fc4b8bc50a'; s.dispatchEvent(new Event('change', { bubbles: true }));
      const ta = document.querySelector('#exportSql'); ta.value = sql; ta.dispatchEvent(new Event('input', { bubbles: true }));
    }, { sql: SQL });
    await p.click('#previewExport');
    await p.waitForFunction(() => { const t = document.querySelector('#exportStatus')?.textContent || ''; return t && t !== '正在预览...'; });
    await p.evaluate(() => window.scrollTo(0, 0));
    await p.screenshot({ path: path.join(__dirname, 'shots', `fixed-top-${w}x${h}.png`) });
    await p.evaluate(() => { const d = document.querySelector('.export-dock'); d.scrollIntoView({ block: 'end' }); });
    await p.waitForTimeout(400);
    const box = await p.evaluate(() => { const r = document.querySelector('.export-dock').getBoundingClientRect();
      const y = Math.max(0, r.y), hh = Math.min(innerHeight - y, r.height);
      return { x: Math.max(0, r.x), y, width: Math.min(innerWidth - Math.max(0, r.x), r.width), height: hh }; });
    if (box.height > 10) await p.screenshot({ path: path.join(__dirname, 'shots', `fixed-dock-${w}x${h}.png`), clip: box });
    const rb = await p.evaluate(() => { const r = document.querySelector('.export-right').getBoundingClientRect(); return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.min(innerWidth - Math.max(0, r.x), r.width), height: Math.min(innerHeight - Math.max(0, r.y), r.height) }; });
    if (rb.width > 10 && rb.height > 10) await p.screenshot({ path: path.join(__dirname, 'shots', `fixed-right-${w}x${h}.png`), clip: rb });
    await p.close();
  }
  await b.close();
})();
