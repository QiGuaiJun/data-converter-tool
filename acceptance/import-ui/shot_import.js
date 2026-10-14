/**
 * 导入模块 UI 体检 —— 两态（任务列表 / 导入编辑器）× 多视口截图 + 几何探针。
 * 运行：NODE_PATH=... node shot_import.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
const SIZES = [[1920, 1080], [1600, 900], [1440, 900], [1366, 768]];

const PROBE = () => {
  const R = (s) => {
    const e = typeof s === 'string' ? document.querySelector(s) : s;
    if (!e) return null;
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    return {
      x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
      r: Math.round(r.right), b: Math.round(r.bottom),
      fs: cs.fontSize, pad: cs.padding, radius: cs.borderRadius, bg: cs.backgroundColor,
      border: cs.borderColor, display: cs.display, gap: cs.gap,
    };
  };
  const panel = document.querySelector('#importTaskPanel');
  const list = document.querySelector('#importTaskList');
  const items = [...document.querySelectorAll('#importTaskList .module-task-item')];
  const toolbar = document.querySelector('.module-task-toolbar');
  const tbtns = [...document.querySelectorAll('.module-task-toolbar button')];
  const shell = document.querySelector('.import-shell');
  const ws = document.querySelector('.app-workspace');
  const left = document.querySelector('.left-pane');
  const right = document.querySelector('.right-pane');
  return {
    viewport: { w: innerWidth, h: innerHeight },
    workspace: R(ws),
    shell: shell ? { ...R(shell), cols: getComputedStyle(shell).gridTemplateColumns, gap: getComputedStyle(shell).gap, visible: getComputedStyle(shell).display !== 'none' } : null,
    left: left ? { ...R(left), visible: getComputedStyle(left).display !== 'none' } : null,
    right: right ? { ...R(right), visible: getComputedStyle(right).display !== 'none' } : null,
    panel: panel ? { ...R(panel), cls: panel.className } : null,
    toolbar: toolbar ? { ...R(toolbar), cls: toolbar.className } : null,
    toolbarButtons: tbtns.map((b) => ({ text: b.textContent.trim(), disabled: b.disabled, ...R(b) })),
    toolbarButtonHeights: [...new Set(tbtns.map((b) => Math.round(b.getBoundingClientRect().height)))],
    toolbarWraps: tbtns.length ? Math.round(Math.max(...tbtns.map((b) => b.getBoundingClientRect().bottom)) - Math.min(...tbtns.map((b) => b.getBoundingClientRect().top))) : null,
    taskList: list ? { ...R(list), cls: list.className, count: items.length } : null,
    taskItem: items[0] ? R(items[0]) : null,
    taskItemHeights: [...new Set(items.map((i) => Math.round(i.getBoundingClientRect().height)))],
    taskItemWidths: [...new Set(items.map((i) => Math.round(i.getBoundingClientRect().width)))],
    firstItemName: (() => {
      const n = document.querySelector('#importTaskList .task-item-name');
      return n ? { text: n.textContent, ...R(n) } : null;
    })(),
    editor: document.querySelector('.module-task-editor') ? R('.module-task-editor') : null,
    docScrollW: document.documentElement.scrollWidth,
    horizontalOverflow: document.documentElement.scrollWidth - innerWidth,
  };
};

(async () => {
  const browser = await chromium.launch();
  const out = {};
  for (const [w, h] of SIZES) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    const errs = [];
    page.on('pageerror', (e) => errs.push(String(e)));
    await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(400);
    out[`${w}x${h}/list`] = await page.evaluate(PROBE);
    await page.screenshot({ path: path.join(OUT, `import-list-${w}x${h}.png`) });

    // 切到编辑器态（点「新增导入」）
    const newBtn = await page.$('#newImportTask');
    if (newBtn) {
      await newBtn.click();
      await page.waitForTimeout(500);
    }
    out[`${w}x${h}/editor`] = await page.evaluate(PROBE);
    await page.screenshot({ path: path.join(OUT, `import-editor-${w}x${h}.png`) });

    console.log(`\n=== ${w}x${h} ===`);
    console.log(`  列表态  卡片 ${JSON.stringify(out[`${w}x${h}/list`].taskItem)}`);
    const L = out[`${w}x${h}/list`];
    console.log(`  list: panel=${JSON.stringify(L.panel)} toolbar=${JSON.stringify(L.toolbar)}`);
    console.log(`        按钮高度=${JSON.stringify(L.toolbarButtonHeights)} 换行占用高=${L.toolbarWraps} 列表=${JSON.stringify(L.taskList)}`);
    console.log(`        卡片高=${JSON.stringify(L.taskItemHeights)} 宽=${JSON.stringify(L.taskItemWidths)} 名称=${JSON.stringify(L.firstItemName)}`);
    console.log(`        左栏=${L.left ? L.left.w : null} 右栏=${L.right ? L.right.w : null} shellVisible=${L.shell?.visible} 横向溢出=${L.horizontalOverflow}`);
    const E = out[`${w}x${h}/editor`];
    console.log(`  editor: shell=${JSON.stringify(E.shell)} left=${E.left ? `${E.left.w}x${E.left.h}@${E.left.x}` : null} right=${E.right ? `${E.right.w}x${E.right.h}@${E.right.x}` : null} 横向溢出=${E.horizontalOverflow}`);
    if (errs.length) console.log('  JS 错误:', errs.join(' | '));
    await page.close();
  }
  fs.writeFileSync(path.join(__dirname, 'import-ui.json'), JSON.stringify(out, null, 2));
  console.log('\n→ import-ui.json + shots/');
  await browser.close();
})();
