/**
 * 导出模块排版体检 —— 修复前后同一脚本对比（Playwright）。
 *
 * 量 5 件事：
 *  1. 文档是否横向溢出（scrollWidth > innerWidth），「开始导出」按钮是否在视口内；
 *  2. 底栏「预览结果」格：#exportPreviewMeta 的实际高度，及其与表头 thead 是否相交；
 *  3. 底栏「导出结果」格：#exportResultMeta 行高 + 日志 div 的行位置；
 *  4. 数据选项「为导出的文件名增加前缀 / 后缀」行：#filePrefix 是否撑破行容器、是否压住后缀；
 *  5. 「备注」textarea 与底部按钮行 .footer-actions 是否相交。
 *
 * 运行：NODE_PATH=... node verify_export_layout.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = path.join(__dirname);
const SQL = fs.readFileSync(path.join(__dirname, '..', 'replay-export-preview-error', 'preview_sample.sql'), 'utf8');
const GOOD = '0f22c4dfd8834709807341fc4b8bc50a';
const SIZES = [[1920, 1080], [1600, 900], [1536, 864], [1440, 900], [1366, 768], [1280, 720], [1120, 640]];

const PROBE = () => {
  const R = (s) => { const e = typeof s === 'string' ? document.querySelector(s) : s; if (!e) return null; const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), r: Math.round(r.right), b: Math.round(r.bottom) }; };
  const hit = (a, b) => (a && b) ? Math.max(0, Math.min(a.r, b.r) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.b, b.b) - Math.max(a.y, b.y)) : 0;
  const cells = [...document.querySelectorAll('.export-dock > div')];
  const c1 = cells[0], c2 = cells[1];
  const meta1 = c1?.querySelector('span'), wrap1 = c1?.querySelector('.table-wrap'), thead1 = c1?.querySelector('thead');
  const meta2 = c2?.querySelector('span'), logs2 = c2?.querySelector('.logs');
  const prefix = document.querySelector('#filePrefix'), suffix = document.querySelector('#fileSuffix');
  const remark = document.querySelector('#exportRemark'), footer = document.querySelector('.footer-actions');
  const runBtn = document.querySelector('#runExport');
  const rowsOf = (el) => el ? getComputedStyle(el).gridTemplateRows : null;
  return {
    docScrollW: document.documentElement.scrollWidth, innerW: innerWidth, scrollX: Math.round(scrollX),
    horizontalOverflow: document.documentElement.scrollWidth - innerWidth,
    runBtn: R(runBtn), runBtnInViewport: runBtn ? (runBtn.getBoundingClientRect().right <= innerWidth + 1 && runBtn.getBoundingClientRect().left >= 0) : null,
    cell1: { rect: R(c1), rows: rowsOf(c1) },
    cell2: { rect: R(c2), rows: rowsOf(c2) },
    meta1: R(meta1), meta1Text: meta1?.textContent, thead1: R(thead1),
    meta1OverThead: hit(R(meta1), R(thead1)),
    wrap1Top: R(wrap1)?.y,
    meta2: R(meta2), logs2: R(logs2), meta2OverLogs: hit(R(meta2), R(logs2)),
    prefixRow: { prefix: R(prefix), suffix: R(suffix), parent: R(prefix?.parentElement),
                 prefixOutOfParent: prefix ? Math.max(0, Math.round(prefix.getBoundingClientRect().right - prefix.parentElement.getBoundingClientRect().right)) : null,
                 prefixOverSuffix: hit(R(prefix), R(suffix)) },
    remark: R(remark), footer: R(footer), remarkOverFooter: hit(R(remark), R(footer)),
  };
};

async function openExport(page) {
  await page.goto(`${BASE}/export.html`, { waitUntil: 'networkidle' });
  await page.evaluate(() => {
    document.querySelector('#newExportTask')?.click();
    if (typeof setExportEditorVisible === 'function') setExportEditorVisible(true);
  });
  await page.waitForSelector('#previewExport', { state: 'visible', timeout: 15000 });
  await page.waitForFunction(() => (document.querySelector('#exportConnection')?.options.length || 0) > 1, { timeout: 15000 });
  await page.evaluate(({ conn, sql }) => {
    const s = document.querySelector('#exportConnection');
    s.value = conn; s.dispatchEvent(new Event('change', { bubbles: true }));
    const ta = document.querySelector('#exportSql');
    ta.value = sql; ta.dispatchEvent(new Event('input', { bubbles: true }));
  }, { conn: GOOD, sql: SQL });
  await page.click('#previewExport');
  await page.waitForFunction(() => {
    const t = document.querySelector('#exportStatus')?.textContent || '';
    return t && t !== '正在预览...';
  }, { timeout: 60000 });
}

(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [w, h] of SIZES) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    await openExport(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    const r = await page.evaluate(PROBE);
    out[`${w}x${h}`] = r;
    console.log(`\n=== ${w}x${h}  docScrollW=${r.docScrollW} 横向溢出=${r.horizontalOverflow}px  scrollX=${r.scrollX}  「开始导出」在视口内=${r.runBtnInViewport}  ${JSON.stringify(r.runBtn)}`);
    console.log(`    预览格 rows=${r.cell1.rows}`);
    console.log(`      meta1 ${JSON.stringify(r.meta1)} 高=${r.meta1?.h}  压表头 ${r.meta1OverThead}px²  wrapTop=${r.wrap1Top}`);
    console.log(`    导出格 rows=${r.cell2.rows}`);
    console.log(`      meta2 ${JSON.stringify(r.meta2)}  logs ${JSON.stringify(r.logs2)}  相交 ${r.meta2OverLogs}px²`);
    console.log(`    前缀行 前缀${JSON.stringify(r.prefixRow.prefix)} 后缀${JSON.stringify(r.prefixRow.suffix)} 行容器${JSON.stringify(r.prefixRow.parent)} 撑破=${r.prefixRow.prefixOutOfParent}px 压后缀=${r.prefixRow.prefixOverSuffix}px²`);
    console.log(`    备注${JSON.stringify(r.remark)} 按钮行${JSON.stringify(r.footer)} 相交=${r.remarkOverFooter}px²`);
    await page.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'export-layout.json'), JSON.stringify(out, null, 2));
  console.log('\n→ export-layout.json');
})();
