/**
 * M12-001 复核：`.preview-block` 分隔样式是否真的生效。
 *
 * 不跑真实查询（避免碰任何库）：直接注入带该 class 的节点，读 computedStyle，
 * 再用一个「故意不存在样式的 class」做负向对照 —— 证明探针能区分「有样式」与「无样式」，
 * 避免把「探针失灵」当成「已修复」。
 *
 * 用法：NODE_PATH=<nm> node acceptance/verify_preview_block.js
 */
const { chromium } = require('playwright');

const BASE = process.env.DC_BASE || 'http://127.0.0.1:51978';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  page.on('pageerror', e => errs.push(String(e.message)));
  await page.goto(BASE + '/export.html', { waitUntil: 'networkidle', timeout: 20000 });

  // 注入两块：第一块不是 :last-child，才能读到块间的 10px 分隔；
  // 同时验证最后一块确实被归零（这是真实 DOM 里的形态）。
  const probe = async (cls) => page.evaluate((c) => {
    const wrap = document.createElement('div');
    for (let i = 0; i < 2; i++) {
      const box = document.createElement('div');
      box.className = c;
      const head = document.createElement('div');
      head.className = c + '-head';
      box.appendChild(head);
      wrap.appendChild(box);
    }
    document.body.appendChild(wrap);
    const first = wrap.children[0];
    const last = wrap.children[1];
    const bs = getComputedStyle(first);
    const hs = getComputedStyle(first.firstChild);
    const out = {
      border_width: bs.borderTopWidth,
      border_style: bs.borderTopStyle,
      border_color: bs.borderTopColor,
      margin_bottom_first: bs.marginBottom,
      margin_bottom_last: getComputedStyle(last).marginBottom,
      border_radius: bs.borderRadius,
      head_padding: hs.paddingTop + ' ' + hs.paddingRight,
      head_bg: hs.backgroundColor,
    };
    wrap.remove();
    return out;
  }, cls);

  const real = await probe('preview-block');
  const fake = await probe('no-such-class-for-negative-control');

  const ok = real.margin_bottom_first === '10px' && real.border_width === '1px'
             && real.margin_bottom_last === '0px';
  const control_ok = fake.border_width === '0px' && fake.margin_bottom_first === '0px';

  console.log('实际 class  computed :', JSON.stringify(real));
  console.log('负向对照 computed  :', JSON.stringify(fake));
  console.log('JS 异常            :', errs.length);
  console.log('');
  console.log(`判定 M12-001  : ${ok ? '✅ 已修复（分隔样式生效）' : '❌ 仍未生效'}`);
  console.log(`探针负向对照  : ${control_ok ? '✅ 有效（无样式时确实读到 0px）' : '❌ 探针失灵，结论不可信'}`);
  await browser.close();
  process.exit(ok && control_ok ? 0 : 1);
})().catch(e => { console.error('FATAL', e); process.exit(2); });
