/**
 * 导出预览「标题行锁定（sticky 表头）」验证 —— Playwright。
 *
 * 量两件事：
 *  场景 1（单查询长表）：滚动 .table-wrap 后，thead th 的 top 是否仍贴在滚动容器顶部，
 *                       且表头处最上层元素是 TH 而不是数据行（无穿透）。
 *  场景 2（多查询分块）：块标题 .preview-block-head 吸顶 0，块内列名行吸在 32px，
 *                       滚动第二块时两者都在位、互不覆盖。
 *
 * 运行：NODE_PATH=... node verify_sticky_header.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';
const OUT = __dirname;
const COLS = ['订单号', '门店', '商品', '数量', '金额', '日期', '市场', '渠道', '备注', '状态', '负责人', '区域'];

const PROBE_SINGLE = (cols) => {
  const host = document.querySelector('#exportPreviewTable');
  const data = Array.from({ length: 80 }, (_, i) => cols.map((c, j) => `${c}${i}-${j}`));
  renderTable(host, cols, data);
  host.scrollTop = 0;
  const th = host.querySelector('thead th');
  const table = host.querySelector('table');
  const wrapRect0 = host.getBoundingClientRect();
  const offsetBefore = Math.round(th.getBoundingClientRect().top - wrapRect0.top);

  const maxScroll = host.scrollHeight - host.clientHeight;
  const target = Math.min(400, maxScroll);
  host.scrollTop = target;

  const wrapRect = host.getBoundingClientRect();
  const thRect = th.getBoundingClientRect();
  const cs = getComputedStyle(th);
  // 预览区在底栏，默认可能落在视口外（elementFromPoint 会返回 null），
  // 先把滚动容器自身滚进视口再取点，才能判断表头处有没有被数据行穿透。
  host.scrollIntoView({ block: 'center' });
  const probeRect = host.getBoundingClientRect();
  const probeY = probeRect.top + Math.round(thRect.height / 2);
  const probeX = probeRect.left + 6;
  const hit = (probeY > 0 && probeY < innerHeight)
    ? document.elementFromPoint(probeX, probeY)
    : null;
  return {
    maxScroll,
    appliedScrollTop: host.scrollTop,
    offsetBefore,
    thOffsetFromWrapTop: Math.round(thRect.top - wrapRect.top),
    thBottomOffsetFromWrapTop: Math.round(thRect.top - wrapRect.top + thRect.height),
    headerIntersectsDataRows: thRect.top < wrapRect.top - 1,
    thPosition: cs.position,
    thTop: cs.top,
    thZIndex: cs.zIndex,
    thBackground: cs.backgroundColor,
    wrapOverflow: getComputedStyle(host).overflow,
    tableOverflow: getComputedStyle(table).overflow,
    theadHeight: Math.round(host.querySelector('thead').getBoundingClientRect().height),
    topElementAtHeaderCorner: hit ? `${hit.tagName}.${hit.className}` : null,
  };
};

const PROBE_MULTI = (cols) => {
  const host = document.querySelector('#exportPreviewTable');
  const mk = (name, n) => ({
    ok: true,
    sourceName: name,
    columns: cols,
    rows: Array.from({ length: n }, (_, i) => cols.map((c, j) => `${c}${i}-${j}`)),
    totalRows: 1200 + n,
  });
  renderPreviewTables(host, [mk('中秋试饮活动', 60), mk('会员提报明细', 60)]);
  host.scrollTop = 0;

  const blocks = [...host.querySelectorAll('.preview-block')];
  const b2 = blocks[1];
  const b1 = blocks[0];
  const head1 = b1.querySelector('.preview-block-head');
  const th1 = b1.querySelector('thead th');

  // 滚到第一块中部：块标题吸顶、列名行吸在它下方
  host.scrollTop = 150;
  const wrapRectA = host.getBoundingClientRect();
  const a = {
    scrollTop: host.scrollTop,
    head1Top: Math.round(head1.getBoundingClientRect().top - wrapRectA.top),
    th1Top: Math.round(th1.getBoundingClientRect().top - wrapRectA.top),
    head1Z: getComputedStyle(head1).zIndex,
    th1Z: getComputedStyle(th1).zIndex,
  };

  // 滚到第二块中部：第二块的标题与列名行同样在位
  host.scrollTop = b2.offsetTop + 120;
  const wrapRectB = host.getBoundingClientRect();
  const head2 = b2.querySelector('.preview-block-head');
  const th2 = b2.querySelector('thead th');
  const head2Rect = head2.getBoundingClientRect();
  const th2Rect = th2.getBoundingClientRect();
  const b = {
    scrollTop: host.scrollTop,
    head2Top: Math.round(head2Rect.top - wrapRectB.top),
    th2Top: Math.round(th2Rect.top - wrapRectB.top),
    mutualOverlap: Math.max(0, Math.min(head2Rect.bottom, th2Rect.bottom) - Math.max(head2Rect.top, th2Rect.top)),
    innerWrapOverflow: getComputedStyle(b2.querySelector('.table-wrap')).overflow,
  };
  return { scrolledIntoBlock1: a, scrolledIntoBlock2: b };
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}/export.html`, { waitUntil: 'networkidle' });

  const single = await page.evaluate(PROBE_SINGLE, COLS);
  await page.screenshot({ path: path.join(OUT, '..', 'evidence', 'sticky-header-single.png') });

  const multi = await page.evaluate(PROBE_MULTI, COLS);
  await page.screenshot({ path: path.join(OUT, '..', 'evidence', 'sticky-header-multi.png') });

  const checks = [
    ['单表：th 为 sticky', single.thPosition === 'sticky'],
    ['单表：th top=0', single.thTop === '0px'],
    ['单表：滚动后表头仍贴容器顶(<=1px)', Math.abs(single.thOffsetFromWrapTop) <= 1],
    ['单表：表头未随内容滚走', single.headerIntersectsDataRows === false],
    ['单表：表头处最上层是 TH 而非数据行', /^TH/.test(single.topElementAtHeaderCorner || '')],
    ['单表：表头底色不透明', !/rgba\(0, 0, 0, 0\)|transparent/.test(single.thBackground)],
    ['单表：table overflow 被覆盖为 visible', single.tableOverflow === 'visible'],
    ['单表：滚动容器仍是 .table-wrap', /auto|scroll/.test(single.wrapOverflow)],
    ['多表：块标题吸顶 0', Math.abs(multi.scrolledIntoBlock1.head1Top) <= 1],
    ['多表：第一块列名行吸在标题下方(≈32px)', Math.abs(multi.scrolledIntoBlock1.th1Top - 32) <= 2],
    ['多表：滚到第二块时块标题吸顶 0', Math.abs(multi.scrolledIntoBlock2.head2Top) <= 1],
    ['多表：第二块列名行吸在 32px', Math.abs(multi.scrolledIntoBlock2.th2Top - 32) <= 2],
    ['多表：标题与列名行不重叠', multi.scrolledIntoBlock2.mutualOverlap === 0],
    ['多表：块内 .table-wrap 不再是滚动容器', multi.scrolledIntoBlock2.innerWrapOverflow === 'visible'],
    ['无 JS 报错', errors.length === 0],
  ];

  console.log('=== 场景 1 单查询长表 ===');
  console.log(JSON.stringify(single, null, 2));
  console.log('\n=== 场景 2 多查询分块 ===');
  console.log(JSON.stringify(multi, null, 2));
  console.log('\n=== 判定 ===');
  let failed = 0;
  for (const [name, ok] of checks) {
    if (!ok) failed += 1;
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  }
  if (errors.length) console.log('pageerror:', errors.join(' | '));
  console.log(`\n${checks.length - failed}/${checks.length} 通过`);

  fs.writeFileSync(path.join(OUT, 'sticky-header.json'), JSON.stringify({ single, multi, checks, errors }, null, 2));
  await browser.close();
  process.exit(failed ? 1 : 0);
})();
