/**
 * C 层 UI 类条目复跑（Playwright）· 只针对隔离实例。
 *
 * 覆盖 13 条：M1-007/008/009/010/011、M2-008/063/064/065、M3-005/007/043/045
 *
 * 复跑中确认的产品机制（决定了探针怎么写，不是缺陷）：
 *  1. 导入页/导出页**默认是任务列表视图**，编辑面板未渲染（元素 rect=0×0）→ 必须先点「新增X」进编辑态。
 *  2. `#newImportTask` / `#newExportTask` 是**多态按钮**：
 *     导入：无选中=「新增导入」/ 有选中=「保存修改」
 *     导出：总览=「新增导出」/ 编辑已存=「保存修改」/ 编辑未存=「保存为新任务」
 *  3. 导入编辑器有 **localStorage 草稿**（P2-16），且 `startNewImportTask()` 在**有未保存草稿时故意复用草稿**（P2-32）
 *     → 验证「新增导入清空编辑器」必须先把草稿清掉，否则复用的是草稿（预期行为）。
 *  4. 导出页顶栏 `.export-topbar` 是 `hidden style="display:none"`（刻意隐藏）→ `#exportConnection`、
 *     `#saveExportConfig` 在界面上**不可点击**；保存入口已迁移到 `#newExportTask`。
 *     目标库选择器同样位于该隐藏顶栏内，故复跑时以程序设 `__sqlite` 指向本地库。
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.BASE || 'http://127.0.0.1:51979';
const OUT = path.resolve(__dirname, 'evidence/20260919/ui-replay.json');
const SHOT = path.resolve(__dirname, 'evidence/20260919/screenshots');
const results = [];

function rec(id, ok, detail) {
  results.push({ id, result: ok ? 'PASS' : 'FAIL', detail: String(detail).slice(0, 600) });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${id} :: ${String(detail).slice(0, 165)}`);
}

async function pickSqlite(page) {
  await page.evaluate(() => {
    const sel = document.querySelector('#exportConnection');
    if (!sel) return;
    if (![...sel.options].some((o) => o.value === '__sqlite')) {
      const o = document.createElement('option');
      o.value = '__sqlite';
      o.textContent = '本地 SQLite';
      sel.appendChild(o);
    }
    sel.value = '__sqlite';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.waitForTimeout(400);
}

(async () => {
  fs.mkdirSync(SHOT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e.message).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });

  for (const n of ['M1-连接A', 'M1-连接B', 'M1-连接C']) {
    await page.request.post(`${BASE}/api/connections`, {
      data: { name: n, dbType: 'mysql', host: '127.0.0.1', port: 3306, user: 'root', password: '123456', database: 'dc_p2_test', charset: 'utf8mb4' },
    });
  }

  /* ================= 连接页 ================= */
  await page.goto(`${BASE}/connections.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  const total = await page.locator('#connTbody tr').count();

  try {
    await page.fill('#connListSearch', 'M1-连接B');
    await page.waitForTimeout(700);
    const byName = await page.locator('#connTbody tr').count();
    await page.fill('#connListSearch', 'dc_p2_test');
    await page.waitForTimeout(700);
    const byDb = await page.locator('#connTbody tr').count();
    await page.fill('#connListSearch', '__no_such_keyword__');
    await page.waitForTimeout(700);
    const none = await page.locator('#connTbody tr').count();
    await page.fill('#connListSearch', '');
    await page.waitForTimeout(700);
    const restored = await page.locator('#connTbody tr').count();
    rec('M1-007', byName > 0 && byName < total && none <= 1 && restored === total,
      `初始=${total} 名称筛"连接B"=${byName} 库名筛"dc_p2_test"=${byDb} 无匹配关键词=${none}(空态可能占 1 行) 清空复位=${restored}`);
  } catch (e) { rec('M1-007', false, e.message); }

  try {
    const opts = await page.locator('#connListTypeFilter option').evaluateAll((els) => els.map((e) => ({ v: e.value, t: e.textContent.trim() })));
    const mysqlV = (opts.find((o) => /mysql/i.test(o.v) || /mysql/i.test(o.t)) || {}).v;
    const otherV = (opts.find((o) => o.v && o.v !== 'all' && !/mysql/i.test(o.v)) || {}).v;
    const setSel = async (v) => {
      await page.evaluate((val) => {
        const s = document.querySelector('#connListTypeFilter');
        s.value = val;
        s.dispatchEvent(new Event('change', { bubbles: true }));
      }, v);
      await page.waitForTimeout(800);
      return page.locator('#connTbody tr').count();
    };
    const mysqlRows = await setSel(mysqlV || '');
    const otherRows = otherV ? await setSel(otherV) : null;
    const restored = await setSel('all');
    rec('M1-008', mysqlRows === total && (otherV ? otherRows <= 1 : true) && restored === total,
      `选项=${JSON.stringify(opts)} 筛 mysql=${mysqlRows}(应=${total}) 筛 ${otherV}=${otherRows}(空态可能占 1 行) 复位(all)=${restored}`);
  } catch (e) { rec('M1-008', false, e.message); }

  try {
    await page.click('#connStatusRefreshBtn');
    await page.waitForTimeout(7500);
    const txt = (await page.locator('#connTbody').innerText()).replace(/\s+/g, ' ');
    rec('M1-009', /已连接|未连接/.test(txt), `刷新后状态列=${JSON.stringify(txt.slice(0, 140))}`);
  } catch (e) { rec('M1-009', false, e.message); }

  try {
    const t1 = await page.getAttribute('#connPassword', 'type');
    await page.click('#connPwdEye');
    await page.waitForTimeout(300);
    const t2 = await page.getAttribute('#connPassword', 'type');
    await page.click('#connPwdEye');
    await page.waitForTimeout(300);
    const t3 = await page.getAttribute('#connPassword', 'type');
    rec('M1-010', t1 === 'password' && t2 === 'text' && t3 === 'password', `type ${t1} -> ${t2} -> ${t3}`);
  } catch (e) { rec('M1-010', false, e.message); }

  try {
    await page.click('#connDbTrigger').catch(async () => { await page.click('#connDbSelect'); });
    await page.waitForTimeout(500);
    const items = page.locator('#connDbMenu [data-value], #connDbMenu li, #connDbMenu button');
    const texts = await items.allTextContents();
    const target = texts.find((t) => /postgres|sqlite|oracle/i.test(t));
    let visible = false; let alertText = '';
    if (target) {
      await items.filter({ hasText: target }).first().click();
      await page.waitForTimeout(900);
      visible = await page.locator('#connUnsupportedAlert').isVisible().catch(() => false);
      alertText = await page.locator('#connUnsupportedText').innerText().catch(() => '');
    }
    rec('M1-011', visible && /待开发/.test(alertText),
      `下拉项=${JSON.stringify(texts.slice(0, 8))} 选=${target} 告警可见=${visible} 文案=${JSON.stringify(alertText.slice(0, 70))}`);
  } catch (e) { rec('M1-011', false, e.message); }

  await page.screenshot({ path: path.join(SHOT, 'replay-connections.png') }).catch(() => {});

  /* ================= 导入页 ================= */
  try {
    const html = await (await page.request.get(`${BASE}/index.html`)).text();
    const hasDir = /id="dirInput"[^>]*webkitdirectory/.test(html);
    const hasRec = /id="recursiveDir"/.test(html);
    rec('M2-008', hasDir && hasRec, `dirInput 含 webkitdirectory=${hasDir}；recursiveDir「遍历子目录」=${hasRec}`);
  } catch (e) { rec('M2-008', false, e.message); }

  // M2-063 任务列表选中 → 回填
  await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  try {
    const items = page.locator('#importTaskList [data-id]');
    const n = await items.count();
    let filled = '';
    if (n) {
      await items.first().dblclick();
      await page.waitForTimeout(2000);
      filled = await page.inputValue('#tableName').catch(() => '');
    }
    rec('M2-063', n > 0 && filled !== '', `列表项=${n} 双击打开后 tableName 回填=${JSON.stringify(filled)}`);
  } catch (e) { rec('M2-063', false, e.message); }

  // M2-065 新增导入 → 清空编辑器（须先清掉 localStorage 草稿，否则按设计复用草稿）
  await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);
  try {
    await page.evaluate(() => localStorage.clear());
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await page.click('#newImportTask');
    await page.waitForTimeout(1200);
    const afterNew = await page.inputValue('#tableName').catch(() => '(不可读)');
    const editorVisible = await page.locator('#tableName').isVisible().catch(() => false);
    rec('M2-065', afterNew === '' && editorVisible,
      `清草稿后点「新增导入」→ tableName=${JSON.stringify(afterNew)}（期望空串）编辑器可见=${editorVisible}`);
  } catch (e) { rec('M2-065', false, e.message); }

  // M2-064 相对路径 → 报错（须先选中一条任务，此时按钮为「保存修改」才走保存分支）
  try {
    await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const items = page.locator('#importTaskList [data-id]');
    let btnLabel = '';
    if (await items.count()) {
      await items.first().dblclick();       // 双击才打开编辑器，同时进入「保存修改」分支
      await page.waitForTimeout(1800);
      btnLabel = (await page.locator('#newImportTask').innerText()).trim();
    }
    await page.fill('#importTaskPath', 'relative_only_name.csv');
    await page.waitForTimeout(300);
    await page.click('#newImportTask');
    await page.waitForTimeout(1500);
    const bodyTxt = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
    const m = bodyTxt.match(/.{0,40}完整路径.{0,50}/);
    rec('M2-064', /完整路径/.test(bodyTxt), `按钮文案=${JSON.stringify(btnLabel)} 出现「完整路径」告警=${/完整路径/.test(bodyTxt)}｜片段=${JSON.stringify(m?.[0] || '')}`);
  } catch (e) { rec('M2-064', false, e.message); }

  await page.screenshot({ path: path.join(SHOT, 'replay-import.png') }).catch(() => {});

  /* ================= 导出页 ================= */
  await page.goto(`${BASE}/export.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await pickSqlite(page);
  await page.click('#newExportTask');       // 进编辑态（create）
  await page.waitForTimeout(1200);
  await pickSqlite(page);

  // M3-007 加载 .sql
  try {
    const sqlPath = path.resolve(__dirname, 'replay-20260919/seed/m3_query.sql');
    fs.mkdirSync(path.dirname(sqlPath), { recursive: true });
    fs.writeFileSync(sqlPath, 'select name, amount from export_people order by name;', 'utf8');
    await page.click('#singleQuery');
    await page.waitForTimeout(300);
    await page.setInputFiles('#sqlFileInput', sqlPath);
    await page.waitForTimeout(1000);
    const val = await page.inputValue('#exportSql');
    rec('M3-007', /export_people/.test(val), `.sql 载入 → exportSql=${JSON.stringify(val.slice(0, 100))}`);
  } catch (e) { rec('M3-007', false, e.message); }

  // M3-005 单个查询 → 预览
  try {
    await pickSqlite(page);
    await page.fill('#exportSql', 'select name, amount from export_people order by name');
    await page.click('#previewExport');
    await page.waitForTimeout(3000);
    const meta = (await page.locator('#exportPreviewMeta').innerText().catch(() => '')).replace(/\s+/g, ' ');
    const tbl = (await page.locator('#exportPreviewTable').innerText().catch(() => '')).replace(/\s+/g, ' ');
    rec('M3-005', /Alice/.test(tbl) && /Carol/.test(tbl), `预览元数据=${JSON.stringify(meta.slice(0, 70))} 结果=${JSON.stringify(tbl.slice(0, 120))}`);
  } catch (e) { rec('M3-005', false, e.message); }

  // M3-045 导出结果区
  try {
    await pickSqlite(page);
    await page.fill('#outputName', 'ui_replay_export');
    await page.waitForTimeout(300);
    await page.click('#runExport');
    await page.waitForTimeout(6000);
    const meta = (await page.locator('#exportResultMeta').innerText().catch(() => '')).replace(/\s+/g, ' ');
    const body = (await page.locator('#exportResults').innerText().catch(() => '')).replace(/\s+/g, ' ');
    const links = await page.locator('#exportResults a').count();
    rec('M3-045', /ui_replay_export/.test(body), `结果元数据=${JSON.stringify(meta.slice(0, 60))} 含文件名=${/ui_replay_export/.test(body)} 链接数=${links} 区域=${JSON.stringify(body.slice(0, 90))}`);
  } catch (e) { rec('M3-045', false, e.message); }

  // M3-043 任务名留空 → 保存报错（入口是 #newExportTask，此刻为「保存为新任务」）
  try {
    await page.fill('#exportTaskName', '');
    await page.fill('#exportSql', 'select 1 as value');
    await pickSqlite(page);
    await page.waitForTimeout(300);
    const label = (await page.locator('#newExportTask').innerText()).trim();
    await page.click('#newExportTask');
    await page.waitForTimeout(2000);
    const status = (await page.locator('#exportStatus').innerText().catch(() => '')).replace(/\s+/g, ' ');
    const bodyTxt = (await page.locator('body').innerText()).replace(/\s+/g, ' ');
    const hit = /请填写任务名称|先填写|任务名称/.test(status) || /请填写任务名称|先填写任务名称/.test(bodyTxt);
    rec('M3-043', hit, `按钮文案=${JSON.stringify(label)} 状态栏=${JSON.stringify(status.slice(0, 80))} 命中报错=${hit}`);
  } catch (e) { rec('M3-043', false, e.message); }

  await page.screenshot({ path: path.join(SHOT, 'replay-export.png') }).catch(() => {});

  await browser.close();

  const agg = {
    pass: results.filter((r) => r.result === 'PASS').length,
    fail: results.filter((r) => r.result === 'FAIL').length,
    total: results.length,
  };
  fs.writeFileSync(OUT, JSON.stringify({
    generatedAt: new Date().toISOString().replace('T', ' ').slice(0, 19),
    base: BASE, pageErrors, consoleErrors, aggregate: agg, results,
  }, null, 2), 'utf8');
  console.log(`\n==== UI 复跑 PASS ${agg.pass} / FAIL ${agg.fail}（共 ${agg.total}）｜pageerror=${pageErrors.length} consoleError=${consoleErrors.length}`);
  console.log(`报告: ${OUT}`);
  process.exit(agg.fail ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });
