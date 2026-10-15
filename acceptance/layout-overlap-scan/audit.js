/**
 * 全站排版体检（Playwright）· 只读，不改代码。
 *
 * 三类问题，全部用几何量判定：
 *  A. 重叠（overlap）   —— 两个互不为祖先的可见元素，交叠面积占较小者 >10% 且 >200px²
 *  B. 内容被裁切        —— overflow:hidden 的容器 scrollH > clientH+2（内容看不见且无法滚）
 *  C. 溢出父容器        —— 子元素 rect 超出父 rect（父 overflow 为 visible，故直接盖到外面）
 *
 * 已知「故意叠加」的白名单（不算问题）：SQL 语法高亮层 pre.sql-editor-hl 及其子树、
 * 下拉菜单/弹层的展开态、右下角外观开关、粘性底栏本身。
 *
 * 运行：
 *   NODE_PATH=... node audit.js [w x h] [w x h] ...
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = path.join(__dirname);
const SHOTS = path.join(OUT, 'shots');
const SQL = fs.readFileSync(path.join(__dirname, '..', 'replay-export-preview-error', 'preview_sample.sql'), 'utf8');
const GOOD = '0f22c4dfd8834709807341fc4b8bc50a';

const PAGES = [
  ['index', '/'], ['export', '/export.html'], ['tables', '/tables.html'], ['query', '/query.html'],
  ['sync', '/sync.html'], ['jobs', '/jobs.html'], ['schedule', '/schedule.html'],
  ['connections', '/connections.html'], ['api', '/api.html'], ['audit', '/audit.html'],
  ['docs', '/docs.html'], ['users', '/users.html'], ['feedback', '/feedback.html'],
];
const SIZES = (process.argv.slice(2).filter((a) => /^\d+x\d+$/.test(a))).map((s) => s.split('x').map(Number));
if (!SIZES.length) SIZES.push([1440, 900], [1080, 560]);

const DETECT = () => {
  const d = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
    [...(el.classList || [])].slice(0, 2).map((c) => '.' + c).join('');
  const R = (el) => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  // 白名单：故意叠加的装饰层 —— SQL 高亮层、外观开关、输入框内嵌图标（清空/眼睛/加载中）
  const WHITELIST = (el) => !!el.closest('pre.sql-editor-hl, .sql-editor-hl, .appearance-dock, .theme-dock, .conn-suffix-btn, .conn-spinner, .input-suffix, .field-suffix');
  const visible = (el) => {
    const t = el.tagName.toLowerCase();
    if (['script', 'style', 'option', 'br', 'link', 'meta', 'head'].includes(t)) return false;
    const c = getComputedStyle(el);
    if (c.display === 'none' || c.visibility === 'hidden' || parseFloat(c.opacity || '1') < 0.05) return false;
    const r = el.getBoundingClientRect();
    // 高度为 0 但有溢出内容（scrollHeight>0）也要算 —— 这正是「文字压到别人身上」的形态
    return r.width >= 2 && (r.height >= 2 || el.scrollHeight > 2);
  };

  // 实际可见（被画出来）的区域：自身盒 + 溢出可见部分，再与所有裁剪祖先求交。
  // 不这么做的话，overflow:auto 里被裁掉的宽表格会被误判成「压在隔壁栏上」。
  const rectOf = (r) => ({ x: r.x, y: r.y, w: r.width, h: r.height, l: r.left, t: r.top, rt: r.right, b: r.bottom });
  const paintRect = (el) => {
    const br = el.getBoundingClientRect();
    const c = getComputedStyle(el);
    let w = br.width, h = br.height;
    if (/^visible$/.test(c.overflow)) {
      if (el.scrollHeight > br.height + 1) h = el.scrollHeight;
      if (el.scrollWidth > br.width + 1) w = br.width === 0 ? el.scrollWidth : Math.max(w, 0);
    }
    let R = { x: br.x, y: br.y, w, h, l: br.x, t: br.y, rt: br.x + w, b: br.y + h };
    let p = el.parentElement;
    while (p && p !== document.documentElement) {
      const pc = getComputedStyle(p);
      const clipped = !/^visible$/.test(pc.overflow) || !/^visible$/.test(pc.overflowX) || !/^visible$/.test(pc.overflowY);
      if (clipped) {
        const pr = p.getBoundingClientRect();
        const l = Math.max(R.l, pr.left), t = Math.max(R.t, pr.top);
        const rt = Math.min(R.rt, pr.right), b = Math.min(R.b, pr.bottom);
        R = { x: l, y: t, w: Math.max(0, rt - l), h: Math.max(0, b - t), l, t, rt, b };
        if (R.w <= 0 || R.h <= 0) return null;
      }
      p = p.parentElement;
    }
    // 滚动视口裁剪（sticky/fixed 之外的普通内容也可能滚出视口）
    const l = Math.max(R.l, 0), t = Math.max(R.t, 0);
    const rt = Math.min(R.rt, innerWidth), b = Math.min(R.b, innerHeight);
    R = { x: l, y: t, w: Math.max(0, rt - l), h: Math.max(0, b - t), l, t, rt, b };
    return R.w > 0 && R.h > 0 ? R : null;
  };
  const all = [...document.querySelectorAll('body *')].filter((e) => visible(e) && !WHITELIST(e));
  const boxes = new Map();
  for (const el of all) boxes.set(el, paintRect(el));

  // A. 重叠
  const overlap = [];
  for (let i = 0; i < all.length; i++) {
    for (let j = i + 1; j < all.length; j++) {
      const A = all[i], B = all[j];
      if (A.contains(B) || B.contains(A)) continue;
      const a = boxes.get(A), b = boxes.get(B);
      if (!a || !b) continue;
      const ox = Math.min(a.rt, b.rt) - Math.max(a.l, b.l);
      const oy = Math.min(a.b, b.b) - Math.max(a.t, b.t);
      if (ox <= 0 || oy <= 0) continue;
      const area = ox * oy, minA = Math.min(a.w * a.h, b.w * b.h);
      if (area < 200 || area / minA < 0.1) continue;
      const bg = (el) => getComputedStyle(el).backgroundColor;
      overlap.push({
        a: d(A), b: d(B), aRect: R(A), bRect: R(B),
        aPaint: [Math.round(a.l), Math.round(a.t), Math.round(a.w), Math.round(a.h)],
        bPaint: [Math.round(b.l), Math.round(b.t), Math.round(b.w), Math.round(b.h)],
        area: Math.round(area),
        sharePct: Math.round((area / minA) * 100), aBg: bg(A), bBg: bg(B),
        aZ: getComputedStyle(A).zIndex, bZ: getComputedStyle(B).zIndex,
      });
    }
  }
  overlap.sort((x, y) => y.sharePct * y.area - x.sharePct * x.area);

  // B. overflow:hidden 容器把内容吃掉了
  const clipped = [];
  for (const el of all) {
    const c = getComputedStyle(el);
    if (!/^(hidden|clip)$/.test(c.overflowY)) continue;
    if (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2) {
      clipped.push({ el: d(el), client: [el.clientWidth, el.clientHeight], scroll: [el.scrollWidth, el.scrollHeight], rect: R(el), text: (el.textContent || '').replace(/\s+/g, ' ').slice(0, 60) });
    }
  }

  // C. 子元素溢出父容器（父不滚动）
  const overflow = [];
  for (const el of all) {
    const p = el.parentElement;
    if (!p || p === document.body || p === document.documentElement) continue;
    const pc = getComputedStyle(p);
    if (!/^visible$/.test(pc.overflow) && !/^visible$/.test(pc.overflowX)) continue;
    const pr = p.getBoundingClientRect(), er = el.getBoundingClientRect();
    const dx = Math.max(0, pr.left - er.left) + Math.max(0, er.right - pr.right);
    const dy = Math.max(0, pr.top - er.top) + Math.max(0, er.bottom - pr.bottom);
    if (dx < 3 && dy < 3) continue;
    // 只报「明显盖出」的，且父有可见背景/边框（才看得出压痕）
    if (dx * er.height + dy * er.width < 400) continue;
    overflow.push({ el: d(el), parent: d(p), rect: R(el), parentRect: R(p), dx: Math.round(dx), dy: Math.round(dy) });
  }
  return { overlap: overlap.slice(0, 25), overlapCount: overlap.length, clipped: clipped.slice(0, 25), clippedCount: clipped.length, overflow: overflow.slice(0, 25), overflowCount: overflow.length, winH: innerHeight, docH: document.documentElement.scrollHeight };
};

async function enterExportEditor(page) {
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
  fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch();

  // 开启认证的实例（如流实验收沙箱）需要先登录：设 DCT_USER / DCT_PASS 即生效
  let storageState;
  if (process.env.DCT_USER) {
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    await p.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await p.fill('#loginUsername', process.env.DCT_USER);
    await p.fill('#loginPassword', process.env.DCT_PASS || '');
    await p.click('#loginSubmit');
    await p.waitForURL((u) => !u.pathname.includes('login.html'), { timeout: 15000 }).catch(() => {});
    storageState = await ctx.storageState();
    await ctx.close();
    console.log(`[auth] 已登录 ${process.env.DCT_USER}`);
  }

  const report = { base: BASE, generatedAt: new Date().toISOString(), runs: [] };
  for (const [w, h] of SIZES) {
    for (const [name, url] of PAGES) {
      const page = await browser.newPage({ viewport: { width: w, height: h }, storageState });
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e.message).slice(0, 200)));
      const row = { page: name, url, size: `${w}x${h}`, jsErrors: errs };
      try {
        await page.goto(`${BASE}${url}`, { waitUntil: 'networkidle', timeout: 30000 });
        await page.waitForTimeout(600);
        if (name === 'export') await enterExportEditor(page);
        Object.assign(row, await page.evaluate(DETECT));
        const shot = `audit-${name}-${w}x${h}.png`;
        await page.screenshot({ path: path.join(SHOTS, shot) });
        row.shot = shot;
        console.log(`[${name} ${w}x${h}] 重叠 ${row.overlapCount} | 裁切 ${row.clippedCount} | 溢出 ${row.overflowCount}${errs.length ? ' | JS错误 ' + errs.length : ''}`);
        for (const o of row.overlap.slice(0, 3)) console.log(`     重叠 ${o.sharePct}% ${o.area}px² ${o.a}${JSON.stringify(o.aRect)} <-> ${o.b}${JSON.stringify(o.bRect)}`);
        for (const o of row.clipped.slice(0, 3)) console.log(`     裁切 ${o.el}${JSON.stringify(o.rect)} client=${o.client} scroll=${o.scroll}`);
      } catch (e) {
        row.error = String(e.message).slice(0, 200);
        console.log(`[${name} ${w}x${h}] 失败 ${row.error}`);
      }
      report.runs.push(row);
      await page.close();
    }
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'audit-result.json'), JSON.stringify(report, null, 2));
  console.log('\n→', path.join(OUT, 'audit-result.json'));
  process.exit(0);
})();
