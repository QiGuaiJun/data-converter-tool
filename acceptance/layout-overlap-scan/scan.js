/**
 * 排版重叠扫描（Playwright）· 只读，不改任何代码。
 *
 * 背景（2026-10-12 业主反馈）：导出页「预览结果」区域组件叠加重合，影响观感；
 * 顺带要求体检整个项目的组件排版。
 *
 * 做法：真实加载每个页面 → 收集所有可见元素的 getBoundingClientRect →
 * 两两求交，报出「交叠面积占较小元素 >8% 且 >24px²」且互不为祖先的元素对。
 * 用几何量说话，不靠肉眼猜。
 *
 * 运行：
 *   NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules" \
 *   "C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe" scan.js [phase]
 *   phase: export（默认，导出页多尺寸）/ all（全站两尺寸）/ 省略=两者都跑
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = path.join(__dirname);
const SHOTS = path.join(OUT, 'shots');
const SQL_FILE = path.join(__dirname, '..', 'replay-export-preview-error', 'preview_sample.sql');
const SQL = fs.readFileSync(SQL_FILE, 'utf8');
const GOOD = '0f22c4dfd8834709807341fc4b8bc50a'; // sr / lcdp_fe

const EXPORT_SIZES = [
  [1920, 1080], [1600, 900], [1440, 900], [1366, 768], [1280, 720], [1152, 648], [1080, 560],
];
const PAGES = [
  ['index', '/'], ['export', '/export.html'], ['tables', '/tables.html'], ['query', '/query.html'],
  ['sync', '/sync.html'], ['jobs', '/jobs.html'], ['schedule', '/schedule.html'],
  ['connections', '/connections.html'], ['api', '/api.html'], ['audit', '/audit.html'],
  ['docs', '/docs.html'], ['users', '/users.html'], ['feedback', '/feedback.html'],
];

const DETECT = () => {
  const desc = (el) => {
    const id = el.id ? '#' + el.id : '';
    const cls = [...(el.classList || [])].slice(0, 2).map((c) => '.' + c).join('');
    return el.tagName.toLowerCase() + id + cls;
  };
  const all = [...document.querySelectorAll('body *')].filter((el) => {
    const t = el.tagName.toLowerCase();
    if (t === 'script' || t === 'style' || t === 'option' || t === 'br' || t === 'link' || t === 'meta') return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (parseFloat(cs.opacity || '1') < 0.05) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    return true;
  }).map((el) => ({ el, r: el.getBoundingClientRect() }));

  const hits = [];
  for (let i = 0; i < all.length; i++) {
    for (let j = i + 1; j < all.length; j++) {
      const A = all[i], B = all[j];
      if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
      const ox = Math.min(A.r.right, B.r.right) - Math.max(A.r.left, B.r.left);
      const oy = Math.min(A.r.bottom, B.r.bottom) - Math.max(A.r.top, B.r.top);
      if (ox <= 0 || oy <= 0) continue;
      const area = ox * oy;
      const minArea = Math.min(A.r.width * A.r.height, B.r.width * B.r.height);
      if (area < 24 || area / minArea < 0.08) continue;
      // 文本元素被另一个有背景的块压住最刺眼，标注一下
      const isText = (x) => /^(strong|span|label|b|small|h1|h2|h3|h4|p|a|button|td|th)$/.test(x.el.tagName.toLowerCase());
      hits.push({
        a: desc(A.el), b: desc(B.el),
        area: Math.round(area),
        sharePct: Math.round((area / minArea) * 100),
        aText: isText(A), bText: isText(B),
        aRect: [Math.round(A.r.x), Math.round(A.r.y), Math.round(A.r.width), Math.round(A.r.height)],
        bRect: [Math.round(B.r.x), Math.round(B.r.y), Math.round(B.r.width), Math.round(B.r.height)],
        zA: getComputedStyle(A.el).zIndex, zB: getComputedStyle(B.el).zIndex,
      });
    }
  }
  hits.sort((p, q) => (q.sharePct * q.area) - (p.sharePct * p.area));

  // 底栏专项：标题 / 表体 / 容器 三者几何关系
  const dock = document.querySelector('.export-dock, .bottom-dock');
  let dockInfo = null;
  if (dock) {
    const d = dock.getBoundingClientRect();
    const box = (sel) => {
      const e = dock.querySelector(sel);
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return { rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], scrollH: e.scrollHeight, clientH: e.clientHeight };
    };
    const first = dock.querySelector(':scope > div');
    const head = first ? first.querySelector('strong') : null;
    dockInfo = {
      dockRect: [Math.round(d.x), Math.round(d.y), Math.round(d.width), Math.round(d.height)],
      dockScrollH: dock.scrollHeight, dockClientH: dock.clientHeight,
      headRect: head ? (() => { const r = head.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })() : null,
      tableWrap: box('.table-wrap'),
      logs: box('.logs'),
    };
  }
  return { hits: hits.slice(0, 40), hitCount: hits.length, dockInfo, scrollY: Math.round(scrollY), docH: document.documentElement.scrollHeight, winH: innerHeight };
};

async function enterExportEditor(page) {
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
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const phase = process.argv[2] || 'both';
  const browser = await chromium.launch();
  const report = { base: BASE, export: [], pages: [] };

  if (phase === 'export' || phase === 'both') {
    for (const [w, h] of EXPORT_SIZES) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e.message)));
      await page.goto(`${BASE}/export.html`, { waitUntil: 'networkidle' });
      await enterExportEditor(page);
      await page.evaluate(() => { const d = document.querySelector('.export-dock'); if (d) d.scrollIntoView({ block: 'nearest' }); });
      const res = await page.evaluate(DETECT);
      const shot = `export-${w}x${h}.png`;
      await page.screenshot({ path: path.join(SHOTS, shot), fullPage: false });
      report.export.push({ size: `${w}x${h}`, shot, pageErrors: errs, ...res });
      console.log(`[export ${w}x${h}] 重叠对 ${res.hitCount} | dockClientH=${res.dockInfo?.dockClientH} dockScrollH=${res.dockInfo?.dockScrollH} | winH=${res.winH} docH=${res.docH}`);
      for (const hh of res.hits.slice(0, 6)) console.log(`      ${hh.sharePct}% ${hh.area}px²  ${hh.a} <-> ${hh.b}`);
      await page.close();
    }
  }

  if (phase === 'all' || phase === 'both') {
    for (const [w, h] of [[1440, 900], [1080, 560]]) {
      for (const [name, url] of PAGES) {
        const page = await browser.newPage({ viewport: { width: w, height: h } });
        const errs = [];
        page.on('pageerror', (e) => errs.push(String(e.message)));
        try {
          await page.goto(`${BASE}${url}`, { waitUntil: 'networkidle', timeout: 30000 });
          await page.waitForTimeout(700);
          if (name === 'export') await enterExportEditor(page);
          const res = await page.evaluate(DETECT);
          const shot = `${name}-${w}x${h}.png`;
          await page.screenshot({ path: path.join(SHOTS, shot), fullPage: false });
          report.pages.push({ page: name, url, size: `${w}x${h}`, shot, pageErrors: errs, ...res });
          console.log(`[${name} ${w}x${h}] 重叠对 ${res.hitCount}${errs.length ? ' | JS错误 ' + errs.length : ''}`);
          for (const hh of res.hits.slice(0, 4)) console.log(`      ${hh.sharePct}% ${hh.area}px²  ${hh.a} <-> ${hh.b}`);
        } catch (e) {
          report.pages.push({ page: name, url, size: `${w}x${h}`, error: String(e.message).slice(0, 200) });
          console.log(`[${name} ${w}x${h}] 加载失败: ${String(e.message).slice(0, 120)}`);
        }
        await page.close();
      }
    }
  }

  await browser.close();
  fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(report, null, 2));
  console.log('\n结果:', path.join(OUT, 'result.json'));
  process.exit(0);
})();
