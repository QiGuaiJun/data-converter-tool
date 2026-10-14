/**
 * 导入编辑器「已选文件」状态截图 —— 用一份真实 CSV 走一遍选择文件，看文件列表/映射/底栏真实观感。
 * 运行：NODE_PATH=... node shot_import_loaded.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(path.join(__dirname, 'fixtures'), { recursive: true });

const CSV = ['日期,门店编码,门店名称,商品名称,数量,金额,备注'];
for (let i = 1; i <= 24; i += 1) {
  CSV.push(`2026-10-${String((i % 28) + 1).padStart(2, '0')},S${1000 + i},测试门店${i},中秋试饮商品${i},${i * 3},${(i * 12.5).toFixed(2)},备注文本${i}`);
}
const csvPath = path.join(__dirname, 'fixtures', '导入样例-中秋试饮.csv');
fs.writeFileSync(csvPath, '\ufeff' + CSV.join('\r\n'), 'utf8');

const SIZES = [[1600, 900], [1440, 900], [1366, 768]];

const PROBE = () => {
  const R = (s) => {
    const e = typeof s === 'string' ? document.querySelector(s) : s;
    if (!e) return null;
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), fs: cs.fontSize, pad: cs.padding, radius: cs.borderRadius, display: cs.display };
  };
  const items = [...document.querySelectorAll('#fileList > *')];
  const preview = document.querySelector('#previewTable');
  const dock = document.querySelector('.bottom-dock');
  return {
    docH: document.documentElement.scrollHeight,
    innerH: innerHeight,
    fileList: R('#fileList'),
    fileListCls: document.querySelector('#fileList')?.className,
    fileItems: items.slice(0, 6).map((e) => ({ tag: e.tagName, cls: e.className, text: (e.textContent || '').slice(0, 40), ...R(e) })),
    fileItemCount: items.length,
    pickerButtons: [...document.querySelectorAll('.picker-buttons .button')].map((b) => ({ text: b.textContent.trim().slice(0, 12), ...R(b) })),
    basicOptions: R('.basic-options'),
    optionGroups: [...document.querySelectorAll('.basic-options .option-group')].map((g) => ({ text: (g.querySelector('strong')?.textContent || '').trim(), ...R(g) })),
    previewTable: preview ? { ...R(preview), cls: preview.className, tableCount: preview.querySelectorAll('table').length, rows: preview.querySelectorAll('tbody tr').length, cols: preview.querySelectorAll('thead th').length } : null,
    dock: R(dock),
    dockCells: [...document.querySelectorAll('.bottom-dock > div')].map((c) => ({ text: (c.querySelector('strong')?.textContent || '').trim(), ...R(c) })),
    leftPickerWrap: R('.left-pane .file-row .picker-buttons'),
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
    await page.click('#newImportTask');
    await page.waitForTimeout(300);
    await page.setInputFiles('#fileInput', csvPath);
    await page.waitForTimeout(2000);
    out[`${w}x${h}`] = await page.evaluate(PROBE);
    await page.screenshot({ path: path.join(OUT, `import-loaded-top-${w}x${h}.png`) });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, `import-loaded-bottom-${w}x${h}.png`) });
    const d = out[`${w}x${h}`];
    console.log(`\n=== ${w}x${h} === docH=${d.docH} innerH=${d.innerH} 文件项=${d.fileItemCount}`);
    console.log('  fileList', JSON.stringify(d.fileList), d.fileListCls);
    console.log('  items', JSON.stringify(d.fileItems));
    console.log('  选择器按钮', JSON.stringify(d.pickerButtons));
    console.log('  目标连接/选项组', JSON.stringify(d.optionGroups));
    console.log('  预览', JSON.stringify(d.previewTable));
    console.log('  dock', JSON.stringify(d.dock), JSON.stringify(d.dockCells));
    if (errs.length) console.log('  JS 错误:', errs.join(' | '));
    await page.close();
  }
  fs.writeFileSync(path.join(__dirname, 'import-loaded.json'), JSON.stringify(out, null, 2));
  await browser.close();
  console.log('\n→ import-loaded.json + shots/');
})();
