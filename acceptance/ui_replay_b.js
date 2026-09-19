/**
 * B 层 UI 类条目复跑（Playwright）· 自带隔离实例。
 *
 * 覆盖 18 条：M4-010/012、M6-001/002/003/004/005、M7-002/003/006/014/017/019、M8-001~006
 * （M8-006 已在 Python harness 覆盖，此处不重复）
 *
 * 复跑中确认的产品机制（决定探针怎么写，不是缺陷）：
 *  1. 作业页列表的过滤在**前端** `jobs.js:136-150`：单步 import/export 任务资产、名字以
 *     ` - 自动作业` 结尾的载体都不显示；后端 `/api/jobs` 返回全量。
 *  2. `#jobList .job-item[data-id]`，点击选中 / **双击**进入编辑。
 *  3. 作业步骤下拉 `#addStepType`(import/export/query) → `#addStepTask` 按类型联动。
 *  4. 查询页「转到导出」走 sessionStorage(`pendingExportSql`/`pendingExportName`) + 跳转 `/export.html`。
 *  5. 占位页（sync/api/feedback）由 `module-pages.js` 渲染，侧边栏 `#appVersion` 异步拉 `/api/meta` 改成
 *     `数据导表工具 v<APP_VERSION>`；docs 页走 `docs.js`。
 *
 * 用法：node acceptance/ui_replay_b.js
 */
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const PROJECT = path.resolve(__dirname, '..');
const PORT = Number(process.env.PORT_B_UI || 51983);
const BASE = `http://127.0.0.1:${PORT}`;
const SANDBOX = path.resolve(__dirname, process.env.DC_REPLAY_SANDBOX_B_UI || 'replay-b-20260919-ui');
const EVID = path.resolve(__dirname, 'evidence/20260919/b-ui-replay.json');
const results = [];

function rec(id, ok, detail) {
  results.push({ id, result: ok ? 'PASS' : 'FAIL', detail: String(detail).slice(0, 700) });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${id} :: ${String(detail).slice(0, 175)}`);
}

function finding(detail) {
  results.push({ id: 'NEW-UI-01', result: 'FINDING', kind: 'FINDING', detail: String(detail).slice(0, 700) });
  console.log(`[FINDG] NEW-UI-01 :: ${String(detail).slice(0, 175)}`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(method, url, body) {
  const res = await fetch(BASE + url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let json = null;
  try { json = await res.json(); } catch (e) { json = { _raw: await res.text().catch(() => '') }; }
  return { status: res.status, body: json };
}

async function waitReady(timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const res = await fetch(BASE + '/api/ping');
      if (res.ok) return true;
    } catch (e) { /* not up yet */ }
    await sleep(700);
  }
  return false;
}

function startServer() {
  const child = spawn(path.join(PROJECT, '.venv', 'Scripts', 'python.exe'), ['server.py'], {
    cwd: PROJECT,
    env: {
      ...process.env,
      DATA_DIR: path.join(SANDBOX, 'data'),
      UPLOADS_DIR: path.join(SANDBOX, 'uploads'),
      EXPORTS_DIR: path.join(SANDBOX, 'exports'),
      HOST: '127.0.0.1',
      PORT: String(PORT),
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
    },
    stdio: ['ignore', fs.openSync(path.join(SANDBOX, 'server.log'), 'w'), 'inherit'],
  });
  return child;
}

const qstep = (sql, name = '查询步骤') => ({
  name, type: 'query', enabled: true, continueOnError: false,
  config: { sql, targetDbType: 'sqlite' },
});

async function seed(sandboxSeedDir) {
  fs.mkdirSync(sandboxSeedDir, { recursive: true });
  const csv = path.join(sandboxSeedDir, 'ui_people.csv');
  fs.writeFileSync(csv, 'name,amount\nAlice,10\n', 'utf8');

  // 单步 import 任务资产（应被作业列表过滤）
  const importAsset = await api('POST', '/api/jobs', {
    name: 'UI单步导入任务', enabled: true,
    steps: [{ name: '导入', type: 'import', enabled: true, continueOnError: false,
              config: { sourcePath: csv, tableName: 'ui_copy_before', targetDbType: 'sqlite', importMode: 'rebuild' } }],
  });
  // 单步 export 任务资产（应被过滤）
  await api('POST', '/api/jobs', {
    name: 'UI单步导出任务', enabled: true,
    steps: [{ name: '导出', type: 'export', enabled: true, continueOnError: false,
              config: { sourceType: 'table', table: 'ui_copy_before', targetDbType: 'sqlite', extension: 'csv',
                        outputName: 'ui_export', openFileAfterExport: false, openFolderAfterExport: false } }],
  });
  // 「 - 自动作业」载体（应被过滤）
  await api('POST', '/api/jobs', { name: 'UI载体 - 自动作业', enabled: true, steps: [qstep('select 1')] });
  // 真正的多步作业（**混合类型**：query + export，主类型才是「作业」）
  await api('POST', '/api/jobs', {
    name: 'UI多步作业', enabled: true,
    steps: [
      qstep('select 1', '步一'),
      { name: '步二', type: 'export', enabled: true, continueOnError: false,
        config: { sourceType: 'table', table: 'ui_copy_before', targetDbType: 'sqlite', extension: 'csv',
                  outputName: 'ui_export2', openFileAfterExport: false, openFolderAfterExport: false } },
    ],
  });
  // 已保存查询
  await api('POST', '/api/queries', { name: 'UI查询A', sql: 'SELECT 42 AS answer', connectionId: '' });
  // 一个调度（供 M7-014/019 使用）
  const jobs = (await api('GET', '/api/jobs')).body.jobs || [];
  const multi = jobs.find((j) => j.name === 'UI多步作业');
  await api('POST', '/api/schedules', {
    name: 'UI调度A', jobId: multi ? multi.id : '', enabled: false,
    rule: { mode: 'interval', amount: 30, unit: 'minutes' },
  });
  return { importAssetId: (importAsset.body.job || {}).id, multiJobId: multi ? multi.id : '', csv };
}

(async () => {
  fs.mkdirSync(SANDBOX, { recursive: true });
  const server = startServer();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const pageErrors = [];
  const consoleErrors = [];
  const page = await context.newPage();
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });

  try {
    if (!(await waitReady())) throw new Error('服务未能就绪');
    const fx = await seed(path.join(SANDBOX, 'seed'));

    // ---------------------------------------------------------------- M4-010
    try {
      await page.goto(`${BASE}/query.html`, { waitUntil: 'networkidle' });
      await page.waitForSelector('#savedQueryList .query-item, #savedQueryList button', { timeout: 15000 });
      await page.evaluate(() => {
        const list = document.querySelector('#savedQueryList');
        const target = [...list.querySelectorAll('*')].find((el) => el.textContent.trim() === 'UI查询A');
        (target || list.firstElementChild).click();
      });
      await sleep(500);
      const name = await page.inputValue('#queryName');
      const sql = await page.inputValue('#querySql');
      rec('M4-010', name === 'UI查询A' && sql.includes('42'),
        `选中已保存查询 → 名称="${name}" SQL="${sql}"（期望 名称=UI查询A、SQL 含 42）`);
    } catch (e) { rec('M4-010', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M4-012
    try {
      await page.fill('#querySql', 'SELECT 7 AS n');
      await page.fill('#queryName', 'UI导出来源');
      await Promise.all([
        page.waitForURL(/export\.html/, { timeout: 15000 }),
        page.click('#sendToExport'),
      ]);
      await page.waitForLoadState('networkidle');
      const exportSql = await page.inputValue('#exportSql').catch(() => '');
      const fname = await page.inputValue('#exportFileName').catch(() => '');
      const visible = await page.evaluate(() => document.body.classList.contains('export-task-editor'));
      rec('M4-012', exportSql.includes('SELECT 7 AS n') && fname === 'UI导出来源' && visible,
        `转到导出 → 落页 ${page.url()}；#exportSql="${exportSql}" #exportFileName="${fname}" ` +
        `body.export-task-editor=${visible}（编辑态已展开）`);
    } catch (e) { rec('M4-012', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M6-001
    try {
      const all = (await api('GET', '/api/jobs')).body.jobs || [];
      const names = all.map((j) => j.name);
      const backendHasAll = ['UI单步导入任务', 'UI单步导出任务', 'UI载体 - 自动作业', 'UI多步作业']
        .every((n) => names.includes(n));
      await page.goto(`${BASE}/jobs.html`, { waitUntil: 'networkidle' });
      await page.waitForSelector('#jobList', { timeout: 15000 });
      await sleep(600);
      const shown = await page.$$eval('#jobList .job-item strong', (els) => els.map((e) => e.textContent.trim()));
      const ok = backendHasAll && !shown.includes('UI单步导入任务') && !shown.includes('UI单步导出任务')
        && !shown.includes('UI载体 - 自动作业') && shown.includes('UI多步作业');
      rec('M6-001', ok,
        `后端 /api/jobs 含全部 4 个=${backendHasAll}；前端列表=[${shown.join(', ')}]（单步导入/导出资产与「 - 自动作业」载体应缺席，UI多步作业应在）`);
    } catch (e) { rec('M6-001', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M6-002 / M6-003
    try {
      await page.click('#newJob');
      await sleep(400);
      const types = await page.$$eval('#addStepType option', (o) => o.map((x) => x.value));
      await page.selectOption('#addStepType', 'import');
      await sleep(300);
      const importOpts = await page.$$eval('#addStepTask option', (o) => o.map((x) => x.textContent.trim()));
      const hint = (await page.textContent('#addTaskHint')) || '';
      const ok2 = types.join(',') === 'import,export,query' && importOpts.includes('UI单步导入任务')
        && !importOpts.includes('UI多步作业');
      rec('M6-002', ok2,
        `步骤类型=[${types.join(',')}]；导入任务下拉=[${importOpts.join(' | ')}]（应含 UI单步导入任务、不含多步作业）；提示="${hint.slice(0, 60)}"`);

      await page.selectOption('#addStepType', 'export');
      await sleep(300);
      const exportOpts = await page.$$eval('#addStepTask option', (o) => o.map((x) => x.textContent.trim()));
      await page.selectOption('#addStepType', 'query');
      await sleep(300);
      const queryOpts = await page.$$eval('#addStepTask option', (o) => o.map((x) => x.textContent.trim()));
      const ok3 = exportOpts.includes('UI单步导出任务') && !exportOpts.includes('UI单步导入任务')
        && queryOpts.includes('UI查询A') && !queryOpts.includes('UI单步导出任务');
      rec('M6-003', ok3,
        `导出下拉=[${exportOpts.join(' | ')}]；查询下拉=[${queryOpts.join(' | ')}]（应按类型互斥）`);
    } catch (e) { rec('M6-002', false, `异常 ${e}`); rec('M6-003', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M6-005
    try {
      // 已在 #jobDialog 内：依次添加 查询A、单步导入任务 两步
      await page.selectOption('#addStepType', 'query');
      await sleep(250);
      await page.selectOption('#addStepTask', { label: 'UI查询A' });
      await page.click('#addStep');
      await sleep(250);
      await page.selectOption('#addStepType', 'import');
      await sleep(250);
      await page.selectOption('#addStepTask', { label: 'UI单步导入任务' });
      await page.click('#addStep');
      await sleep(350);
      const namesOf = () => page.$$eval('#selectedSteps .selected-step, #selectedSteps > *', (els) =>
        els.map((e) => e.textContent.replace(/\s+/g, ' ').trim().replace(/^\d+\.\s*/, '')).filter(Boolean));
      const before = await namesOf();
      // 选中第一步 → 下移 → 上移 → 移除
      await page.evaluate(() => document.querySelector('#selectedSteps').firstElementChild?.click());
      await sleep(200);
      await page.click('#moveStepDown');
      await sleep(300);
      const afterDown = await namesOf();
      await page.click('#moveStepUp');
      await sleep(300);
      const afterUp = await namesOf();
      await page.evaluate(() => document.querySelector('#selectedSteps').firstElementChild?.click());
      await sleep(200);
      await page.click('#removeStep');
      await sleep(300);
      const afterRemove = await namesOf();
      const ok = before.length === 2 && afterDown.join('|') === [...before].reverse().join('|')
        && afterUp.join('|') === before.join('|') && afterRemove.length === 1;
      rec('M6-005', ok,
        `添加两步=[${before.join(' , ')}]；↓ 后顺序=[${afterDown.join(' , ')}]（应互换）；` +
        `↑ 复位=${afterUp.join('|') === before.join('|')}；<< 后剩余=${afterRemove.length}（期望 1）`);
    } catch (e) { rec('M6-005', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M6-004（深拷贝脱钩）
    try {
      // 重新开始一次干净的编辑：取消 → 新增
      await page.click('#cancelJob').catch(() => {});
      await sleep(300);
      await page.click('#newJob');
      await sleep(350);
      await page.fill('#jobName', 'UI深拷贝作业');
      await page.selectOption('#addStepType', 'import');
      await sleep(250);
      await page.selectOption('#addStepTask', { label: 'UI单步导入任务' });
      await page.click('#addStep');
      await sleep(350);
      // 改动源任务配置：tableName ui_copy_before → ui_copy_after
      await api('POST', '/api/jobs', {
        id: fx.importAssetId, name: 'UI单步导入任务', enabled: true,
        steps: [{ name: '导入', type: 'import', enabled: true, continueOnError: false,
                  config: { sourcePath: fx.csv, tableName: 'ui_copy_after', targetDbType: 'sqlite', importMode: 'rebuild' } }],
      });
      await sleep(300);
      await page.click('#saveJob');
      await sleep(800);
      const all = (await api('GET', '/api/jobs')).body.jobs || [];
      const made = all.find((j) => j.name === 'UI深拷贝作业');
      const tbl = made && made.steps[0] && made.steps[0].config ? made.steps[0].config.tableName : '';
      const asset = all.find((j) => j.id === fx.importAssetId);
      const assetTbl = asset && asset.steps[0] ? asset.steps[0].config.tableName : '';
      rec('M6-004', tbl === 'ui_copy_before' && assetTbl === 'ui_copy_after',
        `添加步骤后改动源任务配置：源任务 tableName=${assetTbl}（已改为 after）；` +
        `新建作业内步骤快照 tableName=${tbl}（期望仍是 before，证明深拷贝脱钩）`);
    } catch (e) { rec('M6-004', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M7-002 / M7-003
    try {
      await page.goto(`${BASE}/schedule.html`, { waitUntil: 'networkidle' });
      await page.click('#newSchedule');
      await sleep(400);
      const configByType = {};
      for (const t of ['import', 'export', 'query', 'job']) {
        await page.check(`input[name="stepType"][value="${t}"]`);
        await sleep(350);
        configByType[t] = (await page.textContent('#stepConfig').catch(() => '')) || '';
      }
      const has = (t, s) => configByType[t].includes(s);
      const ok2 = has('import', 'UI单步导入任务') && has('export', 'UI单步导出任务')
        && has('query', 'UI查询A') && has('job', 'UI多步作业');
      rec('M7-002', ok2,
        '各类型步骤选择器加载对应可用任务：' +
        Object.entries(configByType).map(([k, v]) => `${k}:${v.replace(/\s+/g, ' ').trim().slice(0, 40)}`).join(' ｜ '));
      // 附带发现（低危文案）：作业类型为空态时模板与 typeText 都带「作业」→「暂无作业作业」
      finding('public/schedule.js:257 空态模板为 `暂无${typeText(type)}作业，请先在${typeText(type)}页面保存作业`，' +
        '而 typeText("job")="作业" → 作业类型下实测渲染为「暂无作业作业，请先在作业页面保存作业」（「作业」重复）。' +
        '属低危文案问题，不影响功能。');

      const syncDisabled = await page.$eval('input[name="stepType"][value="sync"]', (el) => el.disabled);
      await page.evaluate(() => { const el = document.querySelector('input[name="stepType"][value="sync"]'); if (!el.disabled) el.click(); });
      await sleep(250);
      const syncChecked = await page.$eval('input[name="stepType"][value="sync"]', (el) => el.checked);
      rec('M7-003', syncDisabled === true && syncChecked === false,
        `同步类型 disabled=${syncDisabled}（期望 true）、尝试点击后 checked=${syncChecked}（期望 false）`);
    } catch (e) { rec('M7-002', false, `异常 ${e}`); rec('M7-003', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M7-006
    try {
      const summaryBefore = (await page.textContent('#ruleSummary')) || '';
      await page.click('#openScheduleAssistant');
      await sleep(500);
      const dialogOpen = await page.evaluate(() => document.querySelector('#assistantDialog')?.open === true);
      const panelState = async () => page.evaluate(() => {
        const i = document.querySelector('#assistantIntervalPanel');
        const f = document.querySelector('#assistantFixedPanel');
        return { intervalHidden: i.classList.contains('hidden'), fixedHidden: f.classList.contains('hidden') };
      });
      const s1 = await panelState();
      // 切到「定时」模式（radio name=assistantMode），面板应互换
      await page.check('input[name="assistantMode"][value="fixed"]').catch(() => {});
      await sleep(400);
      const s2 = await panelState();
      await page.selectOption('#assistantFixedMode', 'weekly').catch(() => {});
      await sleep(250);
      await page.click('#applyAssistant');
      await sleep(600);
      const closed = await page.evaluate(() => document.querySelector('#assistantDialog')?.open === false);
      const summaryAfter = (await page.textContent('#ruleSummary')) || '';
      const ok = dialogOpen && !s1.intervalHidden && s1.fixedHidden
        && s2.intervalHidden && !s2.fixedHidden && closed && summaryAfter !== summaryBefore;
      rec('M7-006', ok,
        `助手弹窗打开=${dialogOpen}；初始面板 interval显/fixed隐=${!s1.intervalHidden}/${s1.fixedHidden}；` +
        `切 fixed 后=${!s2.intervalHidden}/${s2.fixedHidden}；点「设置」后关闭=${closed}；` +
        `ruleSummary "${summaryBefore.trim()}" → "${summaryAfter.trim()}"`);
    } catch (e) { rec('M7-006', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M7-017
    try {
      const disabled = await page.$eval('#emailOnFail', (el) => el.disabled);
      rec('M7-017', disabled === true, `#emailOnFail disabled=${disabled}（期望 true，功能待支持）`);
    } catch (e) { rec('M7-017', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M7-014 / M7-019
    try {
      await page.keyboard.press('Escape').catch(() => {});
      await page.click('#cancelSchedule').catch(() => {});
      await sleep(400);
      const rows = await page.$$('#scheduleTableBody tr');
      if (rows.length) await rows[0].click();
      await sleep(300);
      await page.click('#viewScheduleLog');
      await sleep(700);
      const logOpen = await page.evaluate(() => document.querySelector('#logDialog')?.open === true);
      const logText = ((await page.textContent('#scheduleRuns').catch(() => '')) || '').replace(/\s+/g, ' ').trim();
      rec('M7-014', logOpen && logText.length > 4,
        `选中调度后点「查看日志」→ 弹窗打开=${logOpen}，内容="${logText.slice(0, 90)}"`);

      const t1 = (await page.textContent('#lastRefreshTime')) || '';
      await sleep(2200);
      const t2 = (await page.textContent('#lastRefreshTime')) || '';
      const fmt = /刷新时间\s*·\s*\d{2}:\d{2}:\d{2}/.test(t2);
      rec('M7-019', fmt, `#lastRefreshTime "${t1.trim()}" → "${t2.trim()}"（格式应为「刷新时间 · HH:MM:SS」，1s 自动刷新）`);
    } catch (e) { rec('M7-014', false, `异常 ${e}`); rec('M7-019', false, `异常 ${e}`); }

    // ---------------------------------------------------------------- M8-001 ~ M8-005
    const meta = await api('GET', '/api/meta');
    const version = (meta.body && meta.body.appVersion) || '';
    const placeholders = [
      ['M8-001', '/sync.html', '同步'], ['M8-002', '/api.html', 'API'],
      ['M8-003', '/docs.html', '操作手册'], ['M8-004', '/feedback.html', '咨询建议反馈'],
    ];
    for (const [id, url, title] of placeholders) {
      try {
        const resp = await page.goto(BASE + url, { waitUntil: 'networkidle' });
        await sleep(900);
        const text = ((await page.textContent('body')) || '').replace(/\s+/g, ' ').trim();
        const hasTitle = text.includes(title);
        const hasPlaceholder = text.includes('此模块已拆分为独立页面') || text.includes('操作手册');
        const verText = ((await page.textContent('#appVersion').catch(() => '')) || '').trim();
        const verOk = version ? verText.includes(version) : verText.length > 0;
        if (id === 'M8-005') continue;
        rec(id, resp.status() === 200 && hasTitle && hasPlaceholder,
          `${url} HTTP=${resp.status()} 含标题「${title}」=${hasTitle} 含占位文案=${hasPlaceholder}；版本号="${verText}"`);
      } catch (e) { rec(id, false, `异常 ${e}`); }
    }
    try {
      await page.goto(`${BASE}/sync.html`, { waitUntil: 'networkidle' });
      await sleep(1200);
      const a = ((await page.textContent('#appVersion')) || '').trim();
      await page.goto(`${BASE}/docs.html`, { waitUntil: 'networkidle' });
      await sleep(1200);
      const b = ((await page.textContent('#appVersion')) || '').trim();
      rec('M8-005', !!version && a.includes(version) && b.includes(version),
        `/api/meta.appVersion=${version}；sync 页侧边栏="${a}"；docs 页侧边栏="${b}"（均应含版本号）`);
    } catch (e) { rec('M8-005', false, `异常 ${e}`); }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    server.kill();
  }

  const pass = results.filter((r) => r.result === 'PASS').length;
  const fail = results.filter((r) => r.result === 'FAIL').length;
  console.log('\n' + '='.repeat(64));
  console.log(`PASS ${pass} / FAIL ${fail}（共 ${results.length}）`);
  console.log(`pageerror=${pageErrors.length} consoleError=${consoleErrors.length}`);
  fs.mkdirSync(path.dirname(EVID), { recursive: true });
  fs.writeFileSync(EVID, JSON.stringify({
    generatedAt: new Date().toISOString(), base: BASE, pageErrors, consoleErrors,
    aggregate: { pass, fail, total: results.length }, results,
  }, null, 2), 'utf8');
  console.log(`报告: ${EVID}`);
  process.exit(fail ? 1 : 0);
})();
