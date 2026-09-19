/**
 * 飞书「问题清单」UI 类条目重新验证（Playwright）。
 *
 * 覆盖：P1-2 / P2-15 / P3-22 / P3-23 / P3-24 / P3-29 / BUG-31
 *
 * 其中 P3-24（背景壁纸导致文字对比度不足）用**真实像素测量**：
 *   playwright 截图 → Node 内建 zlib 解 PNG → 对每个文本元素在矩形内缘采样背景像素
 *   → 与计算样式里的 color 算 WCAG 对比度。不引入第三方依赖。
 *
 * 自带隔离实例（端口 51986），不触碰 runtime/ 生产数据。
 * 用法：node acceptance/ui_replay_feishu.js
 */
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const zlib = require('zlib');
const fs = require('fs');
const path = require('path');

const PROJECT = path.resolve(__dirname, '..');
const PORT = Number(process.env.PORT_F_UI || 51986);
const BASE = `http://127.0.0.1:${PORT}`;
const SANDBOX = path.resolve(__dirname, process.env.DC_REPLAY_SANDBOX_F_UI || 'replay-feishu-ui');
const EVID = path.resolve(__dirname, 'evidence/20260919/feishu-ui-recheck.json');
const results = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rec(id, verdict, detail) {
  results.push({ id, verdict, detail: String(detail).slice(0, 900) });
  console.log(`[${verdict.padEnd(13)}] ${id} :: ${String(detail).slice(0, 175)}`);
}

async function api(method, url, body) {
  const res = await fetch(BASE + url, {
    method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch (e) { json = { _raw: await res.text().catch(() => '') }; }
  return { status: res.status, body: json };
}

async function waitReady(timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try { const r = await fetch(BASE + '/api/ping'); if (r.ok) return true; } catch (e) { /* wait */ }
    await sleep(700);
  }
  return false;
}

function startServer() {
  fs.mkdirSync(SANDBOX, { recursive: true });
  return spawn(path.join(PROJECT, '.venv', 'Scripts', 'python.exe'), ['server.py'], {
    cwd: PROJECT,
    env: {
      ...process.env,
      DATA_DIR: path.join(SANDBOX, 'data'),
      UPLOADS_DIR: path.join(SANDBOX, 'uploads'),
      EXPORTS_DIR: path.join(SANDBOX, 'exports'),
      HOST: '127.0.0.1', PORT: String(PORT), PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1',
    },
    stdio: ['ignore', fs.openSync(path.join(SANDBOX, 'server.log'), 'w'), 'inherit'],
  });
}

// ------------------------------------------------------------------ PNG 解码（纯 zlib）

function decodePng(buf) {
  let pos = 8, width = 0, height = 0, bitDepth = 8, colorType = 6;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos);
    const type = buf.toString('ascii', pos + 4, pos + 8);
    const data = buf.subarray(pos + 8, pos + 8 + len);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      bitDepth = data[8]; colorType = data[9];
    } else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    pos += 12 + len;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const channels = colorType === 6 ? 4 : colorType === 2 ? 3 : colorType === 0 ? 1 : 4;
  const bpp = channels * (bitDepth / 8);
  const stride = width * bpp;
  const out = Buffer.alloc(height * stride);
  let rp = 0;
  for (let y = 0; y < height; y++) {
    const ft = raw[rp++];
    const line = raw.subarray(rp, rp + stride); rp += stride;
    const o = y * stride;
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? out[o + x - bpp] : 0;
      const b = y > 0 ? out[o - stride + x] : 0;
      const c = (x >= bpp && y > 0) ? out[o - stride + x - bpp] : 0;
      let v = line[x];
      if (ft === 1) v = (v + a) & 255;
      else if (ft === 2) v = (v + b) & 255;
      else if (ft === 3) v = (v + ((a + b) >> 1)) & 255;
      else if (ft === 4) {
        const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
        const pr = (pa <= pb && pa <= pc) ? a : (pb <= pc ? b : c);
        v = (v + pr) & 255;
      }
      out[o + x] = v;
    }
  }
  return { width, height, channels, bpp, stride, data: out };
}

function px(img, x, y) {
  x = Math.max(0, Math.min(img.width - 1, Math.round(x)));
  y = Math.max(0, Math.min(img.height - 1, Math.round(y)));
  const o = y * img.stride + x * img.bpp;
  return [img.data[o], img.data[o + 1], img.data[o + 2]];
}

const lum = ([r, g, b]) => {
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const contrast = (c1, c2) => {
  const l1 = lum(c1), l2 = lum(c2);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
};
function parseColor(css) {
  const m = /rgba?\(([^)]+)\)/.exec(css || '');
  if (!m) return null;
  const p = m[1].split(',').map((s) => parseFloat(s.trim()));
  return [p[0], p[1], p[2]];
}

(async () => {
  const server = startServer();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  try {
    if (!(await waitReady())) throw new Error('服务未就绪');

    // 造数据：一个已保存查询 + 一个导入任务（供 P3-22 使用）
    await api('POST', '/api/queries', { name: 'feishu-查询', sql: 'select 1', connectionId: '' });
    const csv = path.join(SANDBOX, 'ui_feishu.csv');
    fs.writeFileSync(csv, 'name,amount\nAlice,10\n', 'utf8');
    await api('POST', '/api/jobs', {
      name: 'feishu-导入任务', enabled: true,
      steps: [{ name: '导入', type: 'import', enabled: true, continueOnError: false,
                config: { sourcePath: csv, tableName: 'f_ui_tbl', targetDbType: 'sqlite', importMode: 'rebuild' } }],
    });

    // 已导入表面板 id：底部 #tableMeta（"N 张表"）与 #tables；
    // 导入走 API 完成（页面内编辑器是双视图、元素在列表态不可见，直接用 API 更稳），
    // 重点是「导入成功后面板不再永远 0 张表」这一缺陷判据。
    const SUBMIT = '#importForm button[type="submit"]';

    // ------------------------------------------------------------- P1-2
    try {
      const res = await api('POST', '/api/preview', undefined);   // 占位，改用 multipart 直传
      // 用 multipart 直接导入，确保沙箱里确有表
      const boundary = '----feishuinode';
      const csvBuf = fs.readFileSync(csv);
      const parts = Buffer.concat([
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="targetDbType"\r\n\r\nsqlite\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="tableName"\r\n\r\nf_ui_tbl\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="importMode"\r\n\r\nrebuild\r\n`),
        Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="ui_feishu.csv"\r\n` +
                    `Content-Type: application/octet-stream\r\n\r\n`),
        csvBuf, Buffer.from('\r\n'),
        Buffer.from(`--${boundary}--\r\n`),
      ]);
      const up = await fetch(BASE + '/api/import', {
        method: 'POST',
        headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
        body: parts,
      });
      const upBody = await up.json().catch(() => ({}));
      await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
      await sleep(2000);

      // 负向对照：编辑面板默认选中的是 MySQL，未选连接时 /api/tables 会 400。
      // 此时面板**绝不能**继续显示「0 张表」——那是原缺陷的骗人表现。
      await page.click('#newImportTask').catch(() => {});
      await sleep(500);
      const mysqlMeta = ((await page.textContent('#tableMeta').catch(() => '')) || '').trim();
      const mysqlStatus = ((await page.textContent('#status').catch(() => '')) || '').trim();
      const silentZero = mysqlMeta === '0 张表';

      // 正向判据：切到「本地 SQLite」后必须列出真实存在的表
      await page.evaluate(() => {
        const radio = document.querySelector('input[name="targetDbType"][value="sqlite"]');
        if (radio) {
          radio.checked = true;
          radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
      await sleep(1500);
      const meta = ((await page.textContent('#tableMeta').catch(() => '')) || '').trim();
      const items = await page.$$eval('#tables *', (els) => els.length).catch(() => 0);
      const n = parseInt((meta.match(/(\d+)\s*张表/) || [])[1] || '0', 10);

      rec('P1-2', up.status === 200 && n > 0 && items > 0 && !silentZero ? 'PASS' : 'FAIL',
        `导入 HTTP=${up.status}（写出 ${(upBody.summary || {}).rowsWritten} 行）；` +
        `默认 MySQL 无连接时 #tableMeta="${mysqlMeta}"（不得再静默显示「0 张表」→ 静默=${silentZero}，` +
        `状态栏：「${mysqlStatus.slice(0, 60)}」）；切到本地 SQLite 后 #tableMeta="${meta}"、#tables 子元素=${items}`);
    } catch (e) { rec('P1-2', 'FAIL', `异常 ${e}`); }

    // ------------------------------------------------------------- P3-24 对比度（真实像素）
    try {
      // 必须在**无遮罩的干净页面**上测：编辑面板/弹窗的 backdrop 会把整页压暗，
      // 那时采样到的"背景"是被压暗的像素，会得出假低对比度。
      await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
      await sleep(1200);
      await page.evaluate(() => {
        const d = document.querySelector('#confirmDialog');
        if (d && d.open) d.close();
      });
      const samples = await page.evaluate(() => {
        const out = [];
        const push = (sel, label) => {
          const el = document.querySelector(sel);
          if (!el) return;
          const r = el.getBoundingClientRect();
          if (r.width < 24 || r.height < 8) return;
          const cs = getComputedStyle(el);
          out.push({ label, color: cs.color, x: r.x, y: r.y, w: r.width, h: r.height,
                     text: (el.textContent || '').trim().slice(0, 24) });
        };
        push('.sidebar-title', '侧边栏标题');
        push('.app-sidebar', '侧边栏');
        push('main.import-shell .panel-title', '主面板标题');
        push('header.app-ribbon', '顶部导航');
        push('.bottom-dock strong', '底部面板标题');
        push('h1', '一级标题');
        document.querySelectorAll('button').forEach((b, i) => {
          if (i < 12) {
            const r = b.getBoundingClientRect();
            // 禁用态控件不受 WCAG 对比度约束（1.4.3 明确豁免），单独统计不参与判据
            if (r.width > 40 && r.height > 18 && r.y > 0 && r.y < 900 && !b.disabled) {
              out.push({ label: `按钮:${(b.textContent || '').trim().slice(0, 10)}`, color: getComputedStyle(b).color,
                         x: r.x, y: r.y, w: r.width, h: r.height });
            }
          }
        });
        return out;
      });
      const shot = await page.screenshot({ fullPage: false });
      const img = decodePng(shot);
      const rows = [];
      for (const s of samples) {
        const tc = parseColor(s.color);
        if (!tc) continue;
        // 在元素矩形内缘取样（避开字形）：上缘 +3px 与下缘 -3px 两条横线
        const picks = [];
        for (const yy of [s.y + 3, s.y + s.h - 3]) {
          for (let k = 1; k <= 5; k++) picks.push(px(img, s.x + (s.w * k) / 6, yy));
        }
        picks.sort((a, b) => lum(a) - lum(b));
        const bg = picks[Math.floor(picks.length / 2)];  // 中位色当背景
        rows.push({ label: s.label, ratio: +contrast(tc, bg).toFixed(2), text: s.text,
                    color: s.color, bg: `rgb(${bg.join(',')})` });
      }
      rows.sort((a, b) => a.ratio - b.ratio);
      const worst = rows[0];
      const bad = rows.filter((r) => r.ratio < 4.5);
      rec('P3-24', worst && worst.ratio >= 4.5 ? 'PASS' : 'FAIL',
        `像素采样 ${rows.length} 处文本：最低对比度 ${worst ? worst.ratio : 'NA'}:1（${worst ? worst.label + ' ' + worst.color + ' 底 ' + worst.bg : ''}）；` +
        `低于 WCAG AA 4.5:1 的有 ${bad.length} 处 → ${bad.slice(0, 4).map((b) => `${b.label}=${b.ratio}`).join(', ')}`);
      fs.writeFileSync(path.join(SANDBOX, 'contrast-detail.json'), JSON.stringify(rows, null, 2), 'utf8');
    } catch (e) { rec('P3-24', 'FAIL', `异常 ${e}`); }

    // ------------------------------------------------------------- P3-29 侧边栏不再挂任务名
    try {
      const tree = await page.$$eval('.module-tree .tree-node', (els) => els.map((e) => e.textContent.trim()));
      const modules = ['新建连接', '导入', '导出', '同步', '查询', '表', '作业', '定时任务'];
      const junk = tree.filter((t) => /feishu-|导入任务|调度名|任务:|副本/.test(t));
      const allModuleLike = tree.every((t) => modules.some((m) => t.includes(m)));
      rec('P3-29', junk.length === 0 && allModuleLike && tree.length > 0 ? 'PASS' : 'FAIL',
        `侧边栏 .module-tree 条目=${tree.length} 个 → [${tree.join(' / ')}]；` +
        `含任务名特征=${junk.length}（期望 0）；全部为模块入口=${allModuleLike}`);
    } catch (e) { rec('P3-29', 'FAIL', `异常 ${e}`); }

    // ------------------------------------------------------------- P2-15 确认框后按钮不永久禁用
    try {
      const dlg = await page.$('#confirmDialog');
      if (!dlg) {
        rec('P2-15', 'MANUAL', '页面未渲染 #confirmDialog，需人工触发确认路径');
      } else {
        await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
        await page.waitForSelector('#newImportTask', { timeout: 15000 });
        await page.click('#newImportTask');        // 先进编辑态，否则编辑面板未渲染
        await sleep(800);
        await page.setInputFiles('#fileInput', csv);
        await sleep(600);
        await page.click('#previewButton');
        await sleep(1800);
        await page.evaluate(() => {
          const r = document.querySelector('input[name="importMode"][value="rebuild"]');
          if (r) { r.checked = true; r.dispatchEvent(new Event('change', { bubbles: true })); }
        });
        await sleep(300);
        await page.click(SUBMIT).catch(() => {});
        await sleep(1200);
        // 注意：#confirmDialog 是 <section class="dialog hidden">，不是原生 <dialog>，
        // 所以判据只能是「hidden 类被移除」，不能用 .open（永远是 undefined）。
        const appeared = await page.evaluate(() => {
          const el = document.querySelector('#confirmDialog');
          return Boolean(el && !el.classList.contains('hidden'));
        });
        if (appeared) {
          await page.click('#confirmDialogOk').catch(() => {});
          await sleep(3000);
        }
        const state = await page.evaluate(() => {
          const b = document.querySelector('#importForm button[type="submit"]');
          const localConfirmUsed = typeof window.confirm === 'function' && window.__nativeConfirmUsed === true;
          return { disabled: b ? b.disabled : null, text: b ? b.textContent.trim() : '', localConfirmUsed };
        });
        const ok = appeared && state.disabled === false;
        rec('P2-15', ok ? 'PASS' : (appeared ? 'FAIL' : 'MANUAL'),
          `自定义确认框 #confirmDialog 出现=${appeared}；点「确认继续」后提交按钮 disabled=${state.disabled}` +
          `（期望 false；原缺陷为走原生 window.confirm 后按钮永久禁用）`);
      }
    } catch (e) { rec('P2-15', 'FAIL', `异常 ${e}`); }

    // ------------------------------------------------------------- P3-22 任务保存后列表刷新 + 路径回显
    try {
      await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
      await page.waitForSelector('#newImportTask', { timeout: 15000 });
      await page.click('#newImportTask');
      await sleep(800);
      const pathInput = await page.$('#importTaskPath');
      const nameInput = await page.$('#importTaskName');
      if (pathInput) await pathInput.fill(csv);
      if (nameInput) await nameInput.fill('feishu-新建任务');
      await sleep(300);
      const saveBtn = await page.$('#saveImportTask');
      if (saveBtn) { await saveBtn.click(); await sleep(1500); }
      const listNames = await page.$$eval('#importTaskList [data-id]', (els) => els.map((e) => e.textContent.trim()));
      const refreshed = listNames.some((t) => t.includes('feishu-新建任务'));
      // 打开任务 → 路径回显
      await page.evaluate(() => {
        const items = [...document.querySelectorAll('#importTaskList [data-id]')];
        const hit = items.find((e) => e.textContent.includes('feishu-新建任务'));
        if (hit) hit.click();
      });
      await sleep(1200);
      const shownPath = (await page.inputValue('#importTaskPath').catch(() => '')) || '';
      rec('P3-22', refreshed && shownPath.length > 3 ? 'PASS' : 'FAIL',
        `保存后列表出现该任务=${refreshed}（列表=${listNames.length} 项）；点开后 #importTaskPath 回显="${shownPath.slice(0, 60)}"`);
    } catch (e) { rec('P3-22', 'FAIL', `异常 ${e}`); }

    // ------------------------------------------------------------- BUG-31 定时弹窗无死连接下拉
    try {
      await page.goto(`${BASE}/schedule.html`, { waitUntil: 'networkidle' });
      await page.click('#newSchedule');
      await sleep(600);
      const info = await page.evaluate(() => {
        const d = document.querySelector('#scheduleDialog');
        const selects = [...d.querySelectorAll('select')].map((s) => ({
          id: s.id, n: s.options.length,
          looksLikeConnection: [...s.options].some((o) => /127\.0\.0\.1|mysql|localhost|:3306|dc_p2/i.test(o.textContent)),
          disabled: s.disabled,
        }));
        const cfg = document.querySelector('#stepConfig');
        const cfgStyle = getComputedStyle(cfg);
        return { selects, cfgTextAlign: cfgStyle.textAlign, cfgDisplay: cfgStyle.display };
      });
      const dead = info.selects.filter((s) => s.looksLikeConnection || s.disabled);
      rec('BUG-31', dead.length === 0 ? 'PASS' : 'FAIL',
        `弹窗内 select=${JSON.stringify(info.selects)}；疑似死连接下拉/禁用项=${dead.length}（期望 0）——` +
        `原缺陷「连接下拉死组件」应表现为弹窗内混入禁用或指向库的连接选择器`);
    } catch (e) { rec('BUG-31', 'FAIL', `异常 ${e}`); }

    rec('P3-23', 'MANUAL',
      '「任务/作业/查询/调度混合显示语义不清」是主观可读性问题：本轮已确认列表过滤与候选类型分离（M6-001/M7-002/BUG-32 实测通过），' +
      '但是否"语义清晰"需人工目视判断');
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    server.kill();
  }

  const tally = {};
  results.forEach((r) => { tally[r.verdict] = (tally[r.verdict] || 0) + 1; });
  console.log('\n' + '='.repeat(64));
  console.log(`逐条判定：${JSON.stringify(tally)}（共 ${results.length} 条）  pageerror=${pageErrors.length}`);
  fs.mkdirSync(path.dirname(EVID), { recursive: true });
  fs.writeFileSync(EVID, JSON.stringify({ generatedAt: new Date().toISOString(), base: BASE,
    pageErrors, aggregate: tally, results }, null, 2), 'utf8');
  console.log(`报告：${EVID}`);
  process.exit(0);
})();
