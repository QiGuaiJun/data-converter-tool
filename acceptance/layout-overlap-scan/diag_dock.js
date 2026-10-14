/**
 * 诊断：导出页底栏（预览结果 / 导出结果）内部盒模型 + 与上方表单的重叠关系。
 * 只读脚本，不改代码。按多个窗口尺寸逐个打印。
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = path.join(__dirname, 'shots');
const SQL = fs.readFileSync(path.join(__dirname, '..', 'replay-export-preview-error', 'preview_sample.sql'), 'utf8');
const GOOD = '0f22c4dfd8834709807341fc4b8bc50a';

const SIZES = [[1920, 1080], [1440, 900], [1366, 768], [1280, 720], [1120, 640], [1080, 560], [1040, 520]];

const probe = () => {
  const R = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const CS = (el) => { const c = getComputedStyle(el); return `${c.display}/${c.position}/gridRow=${c.gridRowStart}:${c.gridRowEnd}/ovf=${c.overflow}/h=${c.height}/minH=${c.minHeight}`; };
  const dock = document.querySelector('.export-dock');
  const cell = dock.querySelector(':scope > div');
  const g = getComputedStyle(cell);
  const kids = [...cell.children].map((el) => ({
    sel: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ([...el.classList].map((c) => '.' + c).join('')),
    rect: R(el), css: CS(el), scrollH: el.scrollHeight, clientH: el.clientHeight,
  }));
  const strong = cell.querySelector('strong');
  const meta = cell.querySelector('#exportPreviewMeta');
  const wrap = cell.querySelector('.table-wrap');
  const thead = wrap.querySelector('thead');
  const hdrRow = wrap.querySelector('thead tr');
  const firstRow = wrap.querySelector('tbody tr');
  return {
    dock: { rect: R(dock), height: getComputedStyle(dock).height, position: getComputedStyle(dock).position,
            rows: getComputedStyle(dock).gridTemplateRows, cols: getComputedStyle(dock).gridTemplateColumns },
    cell: { rect: R(cell), ...{ display: g.display, rows: g.gridTemplateRows, overflow: g.overflow } },
    kids,
    title: strong ? R(strong) : null,
    meta: meta ? { rect: R(meta), css: CS(meta), text: meta.textContent } : null,
    wrapTop: wrap ? R(wrap) : null,
    thead: thead ? R(thead) : null,
    hdrRow: hdrRow ? R(hdrRow) : null,
    firstRow: firstRow ? R(firstRow) : null,
    // 底栏与左侧表单最后一行是否相交
    formLastGroup: (() => {
      const g2 = [...document.querySelectorAll('.export-basic .export-group')].pop();
      return g2 ? { rect: R(g2), text: g2.textContent.replace(/\s+/g, ' ').slice(0, 40) } : null;
    })(),
    winH: innerHeight, scrollY: Math.round(scrollY), docH: document.documentElement.scrollHeight,
  };
};

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const all = [];
  for (const [w, h] of SIZES) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await page.goto(`${BASE}/export.html`, { waitUntil: 'networkidle' });
    await page.evaluate(() => {
      document.querySelector('#newExportTask')?.click();
      if (typeof setExportEditorVisible === 'function') setExportEditorVisible(true);
    });
    await page.waitForSelector('#previewExport', { state: 'visible', timeout: 15000 });
    await page.waitForFunction(() => (document.querySelector('#exportConnection')?.options.length || 0) > 1, { timeout: 15000 });
    await page.evaluate(({ conn, sql }) => {
      const sel = document.querySelector('#exportConnection');
      sel.value = conn; sel.dispatchEvent(new Event('change', { bubbles: true }));
      const ta = document.querySelector('#exportSql');
      ta.value = sql; ta.dispatchEvent(new Event('input', { bubbles: true }));
    }, { conn: GOOD, sql: SQL });
    await page.click('#previewExport');
    await page.waitForFunction(() => {
      const t = document.querySelector('#exportStatus')?.textContent || '';
      return t && t !== '正在预览...';
    }, { timeout: 60000 });

    // 完整页面 + 底栏局部放大
    await page.screenshot({ path: path.join(OUT, `diag-full-${w}x${h}.png`) });
    const box = await page.evaluate(() => {
      const d = document.querySelector('.export-dock');
      const r = d.getBoundingClientRect();
      return { x: Math.max(0, r.x), y: Math.max(0, r.y), width: Math.min(innerWidth, r.width), height: Math.min(innerHeight - Math.max(0, r.y), r.height) };
    });
    if (box.width > 10 && box.height > 10) {
      await page.screenshot({ path: path.join(OUT, `diag-dock-${w}x${h}.png`), clip: box });
    }

    const res = await page.evaluate(probe);
    all.push({ size: `${w}x${h}`, ...res });
    console.log(`\n===== ${w}x${h}  winH=${res.winH} docH=${res.docH}`);
    console.log('  dock   ', res.dock);
    console.log('  cell   ', res.cell);
    console.log('  title  ', res.title, ' meta', res.meta?.rect, ' wrapTop', res.wrapTop);
    console.log('  thead  ', res.thead, ' hdrRow', res.hdrRow, ' firstRow', res.firstRow);
    for (const k of res.kids) console.log('   kid', k.sel, k.rect, k.css, `scroll=${k.scrollH}/${k.clientH}`);
    console.log('  formLastGroup', res.formLastGroup);
    await page.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'dock-diagnosis.json'), JSON.stringify(all, null, 2));
})();
