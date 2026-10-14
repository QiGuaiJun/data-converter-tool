/**
 * 导入模块「缩略（小视口）下内容看不全」体检：
 *  - 横向被裁：scrollWidth > clientWidth 且 overflow 为 hidden/clip
 *  - 纵向被裁：scrollHeight > clientHeight 且 overflow 为 hidden/clip
 *  - 文字截断：元素 scrollWidth > clientWidth（ellipsis / nowrap 挤压）
 *  - 越界：右边界超出最近的卡片/容器
 * 列表态 + 编辑器态，多档小视口。
 * 运行：NODE_PATH=... node probe_clip.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const SIZES = [[1280, 720], [1152, 700], [1080, 560], [1024, 600], [960, 640]];

const SCAN = () => {
  const name = (e) => {
    let s = e.tagName.toLowerCase();
    if (e.id) s += '#' + e.id;
    if (typeof e.className === 'string' && e.className.trim()) s += '.' + e.className.trim().split(/\s+/).slice(0, 3).join('.');
    return s;
  };
  const txt = (e) => (e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' ? (e.placeholder || e.value || '') : (e.textContent || '')).trim().slice(0, 30);
  const r = [];
  const near = (e) => {
    let p = e.parentElement;
    while (p && p !== document.body) {
      const cs = getComputedStyle(p);
      if (cs.overflowY !== 'visible' || cs.overflowX !== 'visible') return p;
      p = p.parentElement;
    }
    return null;
  };
  for (const e of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) continue;
    const rect = e.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    // 自身被裁
    const clipX = e.scrollWidth - e.clientWidth;
    const clipY = e.scrollHeight - e.clientHeight;
    const hiddenX = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
    const hiddenY = cs.overflowY === 'hidden' || cs.overflowY === 'clip';
    if (hiddenX && clipX > 2) {
      r.push({ kind: 'clip-x', el: name(e), text: txt(e), clip: clipX, w: Math.round(rect.width), h: Math.round(rect.height), overflow: `${cs.overflowX}/${cs.overflowY}` });
    }
    if (hiddenY && clipY > 2) {
      r.push({ kind: 'clip-y', el: name(e), text: txt(e), clip: clipY, w: Math.round(rect.width), h: Math.round(rect.height), overflow: `${cs.overflowX}/${cs.overflowY}` });
    }
    // 文本截断（仅对叶子文本 / 控件）
    const leaf = e.children.length === 0 || (e.tagName === 'INPUT' || e.tagName === 'BUTTON' || e.tagName === 'SELECT' || e.tagName === 'LABEL');
    if (leaf && !hiddenX && clipX > 2 && cs.whiteSpace === 'nowrap') {
      r.push({ kind: 'text-cut', el: name(e), text: txt(e), clip: clipX, w: Math.round(rect.width), h: Math.round(rect.height), overflow: `${cs.overflowX}/${cs.overflowY}`, te: cs.textOverflow });
    }
    // 越出最近裁切容器
    const nc = near(e);
    if (nc) {
      const pr = nc.getBoundingClientRect();
      const dx = Math.round(Math.max(0, rect.right - pr.right));
      const dy = Math.round(Math.max(0, rect.bottom - pr.bottom));
      if (dx > 2 || dy > 2) {
        r.push({ kind: 'out-of-parent', el: name(e), text: txt(e), parent: name(nc), dx, dy, w: Math.round(rect.width), h: Math.round(rect.height) });
      }
    }
  }
  // 视口级横向滚动
  const docOverflowX = document.documentElement.scrollWidth - innerWidth;
  // 关键按钮是否在视口内
  const inView = (s) => {
    const e = document.querySelector(s);
    if (!e) return null;
    const rr = e.getBoundingClientRect();
    return { x: Math.round(rr.x), right: Math.round(rr.right), visible: rr.right <= innerWidth + 1 && rr.left >= -1 && rr.width > 0 };
  };
  return {
    docOverflowX,
    runImport: inView('#runImport'),
    previewButton: inView('#previewImport') || inView('#preview'),
    clipped: r,
  };
};

(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [w, h] of SIZES) {
    for (const mode of ['list', 'editor']) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(300);
      if (mode === 'editor') {
        await page.click('#newImportTask');
        await page.waitForTimeout(300);
        await page.setInputFiles('#fileInput', path.join(__dirname, 'fixtures', '导入样例-中秋试饮.csv'));
        await page.waitForTimeout(1200);
      }
      const res = await page.evaluate(SCAN);
      out[`${w}x${h}/${mode}`] = res;
      console.log(`\n=== ${w}x${h} ${mode} === 横向溢出=${res.docOverflowX}px  开始导入按钮可见=${ressafe(res.runImport)}`);
      const groups = {};
      for (const c of res.clipped) groups[c.kind] = (groups[c.kind] || 0) + 1;
      console.log('  分类计数', JSON.stringify(groups));
      const seen = new Set();
      for (const c of res.clipped) {
        const key = c.kind + c.el + c.text;
        if (seen.has(key)) continue;
        seen.add(key);
        console.log(`   [${c.kind}] ${c.el} ${c.clip !== undefined ? 'clip=' + c.clip : `dx=${c.dx} dy=${c.dy}`} ${c.w}x${c.h} ${c.parent ? 'parent=' + c.parent : ''} "${c.text}"`);
      }
      await page.screenshot({ path: path.join(__dirname, 'shots', `narrow-${mode}-${w}x${h}.png`) });
      await page.close();
    }
  }
  fs.writeFileSync(path.join(__dirname, 'narrow-scan.json'), JSON.stringify(out, null, 2));
  await browser.close();
  console.log('\n→ narrow-scan.json');
})();

function ressafe(v) {
  return v ? `${v.visible}(x=${v.x},right=${v.right})` : 'n/a';
}
