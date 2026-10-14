/**
 * 导入模块控件样式清点：把所有可见控件的高度/字号/内边距/圆角/背景/边框统计出来，
 * 找出「不统一」的具体组合。列表态 + 编辑器态都要测。
 * 运行：NODE_PATH=... node collect_controls.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';

const COLLECT = () => {
  const pick = (e) => {
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    const path = (() => {
      const parts = [];
      let n = e;
      while (n && n !== document.body && parts.length < 4) {
        let s = n.tagName.toLowerCase();
        if (n.id) s += '#' + n.id;
        else if (n.className && typeof n.className === 'string') s += '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.');
        parts.unshift(s);
        n = n.parentElement;
      }
      return parts.join(' > ');
    })();
    return {
      tag: e.tagName.toLowerCase(),
      type: e.getAttribute('type') || '',
      cls: typeof e.className === 'string' ? e.className : '',
      id: e.id || '',
      text: (e.tagName === 'INPUT' ? (e.value || e.placeholder || '') : (e.textContent || '')).trim().slice(0, 24),
      path,
      w: Math.round(r.width), h: Math.round(r.height),
      h2: Number(r.height.toFixed(1)),
      fs: cs.fontSize, lh: cs.lineHeight,
      pad: cs.padding, padT: cs.paddingTop, padL: cs.paddingLeft,
      radius: cs.borderRadius, bw: cs.borderTopWidth, bc: cs.borderTopColor,
      bg: cs.backgroundColor, color: cs.color,
      minH: cs.minHeight, hgt: cs.height, margin: cs.margin,
      gap: cs.gap, boxShadow: cs.boxShadow.slice(0, 40),
    };
  };
  const all = [...document.querySelectorAll('button, select, textarea, input, a.button, label.button, .tab')];
  const out = [];
  for (const e of all) {
    const st = getComputedStyle(e);
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    const rec = pick(e);
    if (rec) out.push(rec);
  }
  return out;
};

const tally = (rows) => {
  const keys = ['h2', 'fs', 'pad', 'radius', 'bg', 'bc', 'color'];
  const byTag = {};
  for (const r of rows) {
    const k = r.tag === 'input' ? `input[${r.type || 'text'}]` : r.tag;
    byTag[k] = byTag[k] || { count: 0 };
    const b = byTag[k];
    for (const f of keys) b[f] = b[f] || {};
    b.count += 1;
    for (const f of keys) b[f][r[f]] = (b[f][r[f]] || 0) + 1;
  }
  return byTag;
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  const listRows = await page.evaluate(COLLECT);

  await page.click('#newImportTask');
  await page.waitForTimeout(400);
  await page.setInputFiles('#fileInput', path.join(__dirname, 'fixtures', '导入样例-中秋试饮.csv'));
  await page.waitForTimeout(1500);
  const editorRows = await page.evaluate(COLLECT);

  fs.writeFileSync(path.join(__dirname, 'controls.json'), JSON.stringify({ listRows, editorRows }, null, 2));

  for (const [name, rows] of [['列表态', listRows], ['编辑器态', editorRows]]) {
    console.log(`\n########## ${name}  控件数=${rows.length} ##########`);
    const t = tally(rows);
    for (const [k, v] of Object.entries(t)) {
      const fmt = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).map(([val, n]) => `${val}×${n}`).join(' , ');
      console.log(`\n[${k}] n=${v.count}`);
      console.log(`   高度 : ${fmt(v.h2)}`);
      console.log(`   字号 : ${fmt(v.fs)}`);
      console.log(`   内边距: ${fmt(v.pad)}`);
      console.log(`   圆角 : ${fmt(v.radius)}`);
      console.log(`   底色 : ${fmt(v.bg)}`);
    }
    console.log('\n--- 逐个明细 ---');
    for (const r of rows) {
      console.log(`${String(r.tag + (r.type ? '[' + r.type + ']' : '')).padEnd(16)} h=${String(r.h2).padStart(5)} w=${String(r.w).padStart(4)} fs=${r.fs.padEnd(5)} pad=${r.pad.padEnd(20)} r=${r.radius.padEnd(16)} bg=${r.bg.padEnd(24)} "${r.text}"`.replace(/\s+$/, ''));
    }
  }
  await browser.close();
})();
