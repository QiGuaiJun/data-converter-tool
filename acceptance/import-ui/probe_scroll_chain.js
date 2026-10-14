/**
 * 定位导入页滚动链路：到底是 document 滚动还是 .app-workspace 内滚，
 * 以及 .bottom-dock 的 sticky 为何不生效。
 */
const { chromium } = require('playwright');
const BASE = process.env.DCT_BASE || 'http://127.0.0.1:51978';

const PROBE = () => {
  const info = (s) => {
    const e = document.querySelector(s);
    if (!e) return null;
    const cs = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return {
      h: Math.round(r.height), y: Math.round(r.y),
      clientH: e.clientHeight, scrollH: e.scrollHeight, offsetH: e.offsetHeight,
      overflow: cs.overflow, overflowY: cs.overflowY,
      position: cs.position, height: cs.height, minHeight: cs.minHeight, maxHeight: cs.maxHeight,
      display: cs.display, flex: cs.flex, bottom: cs.bottom, top: cs.top, zIndex: cs.zIndex,
      isScrollContainer: e.scrollHeight > e.clientHeight + 1,
    };
  };
  const chain = [];
  let el = document.querySelector('.bottom-dock');
  while (el && el !== document.documentElement.parentNode) {
    const cs = getComputedStyle(el);
    chain.push({
      tag: el.tagName + (el.className ? '.' + String(el.className).split(' ').join('.') : ''),
      overflow: cs.overflow, overflowY: cs.overflowY, position: cs.position,
      h: Math.round(el.getBoundingClientRect().height),
      clientH: el.clientHeight, scrollH: el.scrollHeight,
      scrolls: el.scrollHeight > el.clientHeight + 1,
    });
    el = el.parentElement;
  }
  return {
    docScrollTop: document.scrollingElement.scrollTop,
    docScrollH: document.scrollingElement.scrollHeight,
    innerH: innerHeight,
    html: info('html'),
    body: info('body'),
    shell: info('.desktop-shell'),
    main: info('.desktop-main'),
    workspace: info('.app-workspace'),
    importShell: info('.import-shell'),
    dock: info('.bottom-dock'),
    dockStickyChain: chain,
  };
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.click('#newImportTask');
  await page.waitForTimeout(400);
  const before = await page.evaluate(PROBE);
  await page.evaluate(() => window.scrollTo(0, 400));
  const dockAfterScroll = await page.evaluate(() => {
    const d = document.querySelector('.bottom-dock').getBoundingClientRect();
    return { top: Math.round(d.top), bottom: Math.round(d.bottom), innerH: innerHeight, visibleInViewport: d.bottom > 0 && d.top < innerHeight };
  });
  console.log(JSON.stringify({ before, dockAfterScroll }, null, 2));
  await browser.close();
})();
