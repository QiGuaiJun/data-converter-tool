/**
 * V6 功能验收 · 全页面按钮走查取证
 *
 * 做法（刻意保守）：只枚举、只点「安全」按钮。
 *   - 枚举：每页所有 button / 链接 / select 的文本、id、禁用、可见、坐标
 *   - 安全点击白名单：新增/添加/查看/预览/打开/详情/手册（打开类），点完按 Esc / 点取消关掉
 *   - 危险按钮（删除/保存/执行/运行/导入/导出/上传/下载/清空/重置/连接测试）**一律不点**，
 *     在清单里标成「未点击·需人工实操」，因为点了会写数据 —— 走查不等于乱点一遍。
 *
 * 服务须已在 BASE 上运行。用法：
 *   NODE_PATH=<node_modules> node acceptance/ui_walkthrough.js
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.DC_BASE || 'http://127.0.0.1:51978';
const OUT = process.env.DC_OUT || 'D:/ProjectDevelopment/data-converter-tool/acceptance/evidence/20260919';
const SHOTS = path.join(OUT, 'screenshots');
const NAV = '/api/module-nav';

const PAGES = [
  ['导入', '/', 'index'],
  ['导出', '/export.html', 'export'],
  ['新建连接', '/connections.html', 'connections'],
  ['同步', '/sync.html', 'sync'],
  ['查询', '/query.html', 'query'],
  ['表', '/tables.html', 'tables'],
  ['作业', '/jobs.html', 'jobs'],
  ['定时任务', '/schedule.html', 'schedule'],
  ['API', '/api.html', 'api'],
  ['操作手册', '/docs.html', 'docs'],
  ['咨询建议反馈', '/feedback.html', 'feedback'],
];

const SAFE_OPEN = /新增|添加|查看|预览|打开|详情|手册|帮助|说明/;
const DANGER = /删除|移除|保存|执行|运行|导入|导出|上传|下载|清空|重置|测试|连接|提交|应用|发布|开始|停止/;

async function enumerate(page) {
  return page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll(
      'button, a[href], input[type=button], input[type=submit], select, [role="button"]'
    ));
    const seen = new Set();
    const out = [];
    for (const el of nodes) {
      const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const visible = cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
      const key = `${el.tagName}|${el.id}|${text}|${Math.round(r.x)},${Math.round(r.y)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        cls: (el.className || '').toString().slice(0, 60),
        text: text.slice(0, 60),
        disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
        visible,
        rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      });
    }
    const counts = {};
    for (const s of ['input', 'textarea', 'table', 'tbody tr', '.card', '.modal', '.dialog']) {
      counts[s] = document.querySelectorAll(s).length;
    }
    return { items: out, counts };
  });
}

async function dialogVisible(page) {
  return page.evaluate(() => {
    const sels = ['.modal', '.dialog', '[role="dialog"]', '.drawer', '.overlay', '.mask'];
    for (const s of sels) {
      for (const el of document.querySelectorAll(s)) {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        if (cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 60 && r.height > 60) return true;
      }
    }
    return false;
  });
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const started = new Date().toISOString();
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const report = { base: BASE, started_at: started, pages: [], console_errors: [], page_errors: [] };

  for (const [label, urlPath, slug] of PAGES) {
    const page = await ctx.newPage();
    const perr = [];
    const cerr = [];
    page.on('pageerror', e => perr.push(String(e.message).slice(0, 300)));
    page.on('console', m => { if (m.type() === 'error') cerr.push(m.text().slice(0, 300)); });

    const rec = { label, path: urlPath, slug, http_status: null, page_errors: perr, console_errors: cerr,
                  counts: {}, buttons: [], clicked: [], skipped_danger: [] };

    try {
      const resp = await page.goto(BASE + urlPath, { waitUntil: 'networkidle', timeout: 20000 });
      rec.http_status = resp ? resp.status() : null;
      await page.waitForTimeout(500);

      const en = await enumerate(page);
      rec.counts = en.counts;
      rec.buttons = en.items;

      const vis = en.items.filter(i => i.visible && !i.disabled);
      // 只点 <button>：导航是 <a href>，点了会跳页，会污染后续页面的走查
      const clickable = vis.filter(i => i.tag === 'button' && SAFE_OPEN.test(i.text) && !DANGER.test(i.text));
      rec.skipped_danger = vis.filter(i => DANGER.test(i.text))
        .map(i => ({ text: i.text, id: i.id, reason: '会写数据或触发外部动作，未点击' })).slice(0, 40);

      for (const b of clickable.slice(0, 6)) {
        const before = await dialogVisible(page);
        let ok = true, err = '';
        try {
          const loc = b.id ? page.locator(`#${b.id}`).first()
                           : page.locator('button', { hasText: b.text }).first();
          await loc.click({ timeout: 4000 });
          await page.waitForTimeout(700);
        } catch (e) { ok = false; err = String(e.message).slice(0, 160); }
        const after = await dialogVisible(page);
        rec.clicked.push({ text: b.text, id: b.id, click_ok: ok, error: err,
                           opened_dialog: !before && after });
        await page.keyboard.press('Escape').catch(() => {});
        await page.waitForTimeout(200);
      }

      await page.screenshot({ path: path.join(SHOTS, `${slug}.png`), fullPage: true });
    } catch (e) {
      rec.fatal = String(e.message).slice(0, 300);
    }

    report.page_errors.push(...perr.map(m => `${slug}: ${m}`));
    report.console_errors.push(...cerr.map(m => `${slug}: ${m}`));
    report.pages.push(rec);
    console.log(`[${slug}] http=${rec.http_status} 控件=${rec.buttons.length} 已点=${rec.clicked.length} 错误=${perr.length}/${cerr.length}`);
    await page.close();
  }

  await browser.close();
  report.finished_at = new Date().toISOString();

  fs.writeFileSync(path.join(OUT, 'walkthrough.json'), JSON.stringify(report, null, 2), 'utf-8');

  const md = [];
  md.push('# 功能验收 · 全页面按钮走查清单');
  md.push('');
  md.push(`- 目标服务：\`${BASE}\``);
  md.push(`- 执行时间：${started}`);
  md.push(`- 页面数：${report.pages.length}`);
  md.push(`- 页面级 JS 异常：**${report.page_errors.length}** ；控制台 error：**${report.console_errors.length}**`);
  md.push('');
  md.push('> 口径说明：**危险按钮（删除/保存/执行/导入/导出/连接测试等）一律未点击**，只做枚举登记。');
  md.push('> 「走查」检验的是页面能否无异常加载、控件是否齐全可用，不是把所有按钮点一遍——那会真写数据。');
  md.push('');
  md.push('| 页面 | HTTP | 控件数 | 可见可用 | 已安全点击 | 打开弹窗 | 危险未点 | JS 异常 |');
  md.push('|---|---|---:|---:|---:|---:|---:|---:|');
  for (const p of report.pages) {
    const vis = p.buttons.filter(b => b.visible && !b.disabled).length;
    const opened = p.clicked.filter(c => c.opened_dialog).length;
    md.push(`| ${p.label} | ${p.http_status} | ${p.buttons.length} | ${vis} | ${p.clicked.length} | ${opened} | ${p.skipped_danger.length} | ${p.page_errors.length} |`);
  }
  md.push('');
  for (const p of report.pages) {
    md.push(`## ${p.label}  \`${p.path}\``);
    md.push('');
    if (p.fatal) md.push(`> ⚠️ 页面级失败：${p.fatal}`);
    md.push(`截图：\`screenshots/${p.slug}.png\``);
    md.push('');
    const vis = p.buttons.filter(b => b.visible);
    md.push('| 控件 | id | 状态 | 文本 |');
    md.push('|---|---|---|---|');
    for (const b of vis.slice(0, 60)) {
      const st = b.disabled ? '禁用' : '可用';
      md.push(`| ${b.tag}${b.cls ? ' .' + b.cls.split(' ')[0] : ''} | \`${b.id}\` | ${st} | ${b.text.replace(/\|/g, '/')} |`);
    }
    if (p.clicked.length) {
      md.push('');
      md.push('**已点击（安全类）**：');
      for (const c of p.clicked) {
        md.push(`- \`${c.text}\` → ${c.click_ok ? '未报错' : '**报错：' + c.error + '**'}${c.opened_dialog ? '（打开了弹窗）' : ''}`);
      }
    }
    md.push('');
  }
  if (report.page_errors.length) {
    md.push('## 页面级 JS 异常汇总');
    md.push('');
    for (const e of report.page_errors) md.push(`- ${e}`);
    md.push('');
  }
  if (report.console_errors.length) {
    md.push('## 控制台错误汇总');
    md.push('');
    for (const e of report.console_errors.slice(0, 60)) md.push(`- ${e}`);
    md.push('');
  }
  fs.writeFileSync(path.join(OUT, '走查清单.md'), md.join('\n'), 'utf-8');
  console.log(`\n完成：${report.pages.length} 页；JS 异常 ${report.page_errors.length}；控制台错误 ${report.console_errors.length}`);
  console.log(`输出：${OUT}`);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
