/**
 * 查询页「SQL 控制台」前端改造验证（Playwright）。
 *
 * 验证对象：
 *   1. renderExecution() 对「多结果集 / 纯 DML / 执行失败」三种响应的渲染
 *   2. 全站通用确认框 window.dcConfirm()（query.js 执行高危 SQL 前用它）
 *   3. 页面加载无 JS 异常、新 DOM 容器存在
 *
 * 说明：查询页要求先选数据库连接，隔离沙箱里没有连接记录，
 * 因此这里做**组件级**验证（直接调用页面内的渲染函数），
 * 真实的「按钮 → needConfirm → 确认 → 执行」链路由 acceptance/probe_query_limits.py
 * 在 API 层覆盖（含一次性令牌复用被拒）。
 *
 * 自带隔离实例（默认端口 51994），不触碰 runtime/。
 * 用法：node acceptance/ui_query_console.js
 */
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const PROJECT = path.resolve(__dirname, '..');
const PORT = Number(process.env.PORT_QUERY_UI || 51994);
const BASE = `http://127.0.0.1:${PORT}`;
const SANDBOX = path.resolve(__dirname, process.env.DC_REPLAY_SANDBOX_QUERY_UI || 'replay-query-ui');
const EVID = path.resolve(__dirname, 'evidence/20260921/query-console-ui.json');
const results = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rec(id, verdict, detail) {
  results.push({ id, verdict, detail: String(detail).slice(0, 600) });
  console.log(`[${verdict.padEnd(6)}] ${id} :: ${String(detail).slice(0, 220)}`);
}

async function waitReady(timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(BASE + '/api/ping');
      if (r.ok) return true;
    } catch (e) { /* 继续等 */ }
    await sleep(600);
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

(async () => {
  const server = startServer();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));

  try {
    if (!(await waitReady())) throw new Error('服务未就绪');

    await page.goto(`${BASE}/query.html`, { waitUntil: 'networkidle' });
    await sleep(800);

    // ---------------------------------------------------------- 页面结构
    try {
      const dom = await page.evaluate(() => ({
        hasSummary: Boolean(document.querySelector('#queryStatementSummary')),
        hasTabs: Boolean(document.querySelector('#queryResultTabs')),
        resultHidden: document.querySelector('#queryStatementSummary').classList.contains('hidden'),
        editorHint: (document.querySelector('.query-name-row span') || {}).textContent || '',
        dcConfirm: typeof window.dcConfirm,
        renderExecution: typeof window.renderExecution,
      }));
      const ok = dom.hasSummary && dom.hasTabs && dom.resultHidden
        && dom.dcConfirm === 'function' && dom.renderExecution === 'function';
      rec('Q-UI-01', ok ? 'PASS' : 'FAIL',
        `语句摘要容器=${dom.hasSummary} 结果集 tab 容器=${dom.hasTabs} 初始隐藏=${dom.resultHidden}；` +
        `dcConfirm=${dom.dcConfirm} renderExecution=${dom.renderExecution}；编辑器提示="${dom.editorHint}"`);
    } catch (e) { rec('Q-UI-01', 'FAIL', `异常 ${e}`); }

    // ---------------------------------------------------------- 多语句 / 多结果集
    try {
      const rendered = await page.evaluate(() => {
        window.renderExecution({
          totals: { statements: 2, resultSets: 2, affectedRows: 0, failed: 0 },
          resultSetCount: 2, affectedRows: 0, failedIndex: 0, elapsedMs: 12, truncated: false,
          message: '共执行 2 条语句，全部成功。',
          columns: ['one'], rows: [['1']], rowCount: 1,
          statements: [
            { index: 1, sql: 'select 1 as one', kind: 'resultset', resultSets: [{ columns: ['one'], rows: [['1']], rowCount: 1, truncated: false }], affectedRows: null, warnings: [], elapsedMs: 5, error: null },
            { index: 2, sql: 'select 2 as two', kind: 'resultset', resultSets: [{ columns: ['two'], rows: [['2']], rowCount: 1, truncated: false }], affectedRows: null, warnings: [], elapsedMs: 7, error: null },
          ],
        });
        return {
          tabs: [...document.querySelectorAll('.query-tab')].map((b) => b.textContent.trim()),
          rows: document.querySelectorAll('.query-statement-row').length,
          headers: [...document.querySelectorAll('#queryResult thead th')].map((t) => t.textContent),
          meta: document.querySelector('#queryResultMeta').textContent,
          status: document.querySelector('#queryStatus').textContent,
        };
      });
      const ok = rendered.tabs.length === 2 && rendered.rows === 2 && rendered.headers.join() === 'one'
        && rendered.meta.includes('2 条语句') && rendered.meta.includes('结果集 2');
      rec('Q-UI-02', ok ? 'PASS' : 'FAIL',
        `结果集 tab=${JSON.stringify(rendered.tabs)}；语句摘要行=${rendered.rows}；当前表格列=${JSON.stringify(rendered.headers)}；` +
        `meta="${rendered.meta}"；状态="${rendered.status}"`);
    } catch (e) { rec('Q-UI-02', 'FAIL', `异常 ${e}`); }

    // ---------------------------------------------------------- 纯 DML（无结果集）
    try {
      const rendered = await page.evaluate(() => {
        window.renderExecution({
          totals: { statements: 1, resultSets: 0, affectedRows: 3, failed: 0 },
          resultSetCount: 0, affectedRows: 3, failedIndex: 0, elapsedMs: 4, truncated: false,
          message: '语句执行成功，影响 3 行。',
          columns: [], rows: [], rowCount: 0,
          statements: [
            { index: 1, sql: 'update t set a=1', kind: 'affected', resultSets: [], affectedRows: 3, warnings: [], elapsedMs: 4, error: null },
          ],
        });
        return {
          summary: document.querySelector('#queryStatementSummary').textContent,
          emptyText: document.querySelector('#queryResult').textContent,
          meta: document.querySelector('#queryResultMeta').textContent,
          tabsHidden: document.querySelector('#queryResultTabs').classList.contains('hidden'),
        };
      });
      const ok = rendered.summary.includes('影响 3 行') && rendered.tabsHidden
        && rendered.emptyText.includes('没有返回结果集') && rendered.meta.includes('影响 3 行');
      rec('Q-UI-03', ok ? 'PASS' : 'FAIL',
        `摘要="${rendered.summary.trim()}"；结果区="${rendered.emptyText.trim()}"；meta="${rendered.meta}"；tabs 隐藏=${rendered.tabsHidden}`);
    } catch (e) { rec('Q-UI-03', 'FAIL', `异常 ${e}`); }

    // ---------------------------------------------------------- 执行失败（第 2 条挂）
    try {
      const rendered = await page.evaluate(() => {
        window.renderExecution({
          totals: { statements: 2, resultSets: 1, affectedRows: 0, failed: 1 },
          resultSetCount: 1, affectedRows: 0, failedIndex: 2, elapsedMs: 9, truncated: false,
          message: '共 2 条语句，第 2 条失败，已回滚该语句：第 2 条语句执行失败：no such table',
          columns: ['one'], rows: [['1']], rowCount: 1,
          statements: [
            { index: 1, sql: 'select 1 as one', kind: 'resultset', resultSets: [{ columns: ['one'], rows: [['1']], rowCount: 1, truncated: false }], affectedRows: null, warnings: [], elapsedMs: 5, error: null },
            { index: 2, sql: 'select * from no_such', kind: 'resultset', resultSets: [], affectedRows: null, warnings: [], elapsedMs: 4, error: '第 2 条语句执行失败：no such table' },
          ],
        });
        return {
          errorRows: document.querySelectorAll('.query-statement-row.error').length,
          status: document.querySelector('#queryStatus').textContent,
          statusClass: document.querySelector('#queryStatus').className,
          meta: document.querySelector('#queryResultMeta').textContent,
        };
      });
      const ok = rendered.errorRows === 1 && rendered.status.includes('执行失败')
        && rendered.statusClass.includes('error') && rendered.meta.includes('第 2 条失败');
      rec('Q-UI-04', ok ? 'PASS' : 'FAIL',
        `失败行标记=${rendered.errorRows}；状态栏="${rendered.status}"（class=${rendered.statusClass}）；meta="${rendered.meta}"`);
    } catch (e) { rec('Q-UI-04', 'FAIL', `异常 ${e}`); }

    // ---------------------------------------------------------- 通用确认框
    try {
      const dialog = await page.evaluate(async () => {
        const promise = window.dcConfirm({
          title: '高危 SQL 确认',
          lines: ['以下语句被判定为高危：', '第 1 条：drop table t', '　· DROP 会不可逆地删除对象或改动权限'],
          okText: '确认执行',
        });
        const box = document.querySelector('#dcConfirmDialog');
        const visible = box && !box.classList.contains('hidden');
        const text = box ? box.textContent : '';
        document.querySelector('#dcConfirmOk').click();
        const confirmed = await promise;
        return { visible, text, confirmed, hiddenAfter: box.classList.contains('hidden') };
      });
      const ok = dialog.visible && dialog.text.includes('DROP') && dialog.confirmed === true && dialog.hiddenAfter;
      rec('Q-UI-05', ok ? 'PASS' : 'FAIL',
        `确认框可见=${dialog.visible}（文案含 DROP=${dialog.text.includes('DROP')}）；点确认返回=${dialog.confirmed}；关闭后隐藏=${dialog.hiddenAfter}`);
    } catch (e) { rec('Q-UI-05', 'FAIL', `异常 ${e}`); }

    try {
      const cancelled = await page.evaluate(async () => {
        const promise = window.dcConfirm({ title: '高危 SQL 确认', lines: ['测试取消路径'] });
        document.querySelector('#dcConfirmCancel').click();
        return await promise;
      });
      rec('Q-UI-06', cancelled === false ? 'PASS' : 'FAIL', `点取消返回=${cancelled}（期望 false）`);
    } catch (e) { rec('Q-UI-06', 'FAIL', `异常 ${e}`); }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    server.kill();
  }

  const tally = {};
  results.forEach((r) => { tally[r.verdict] = (tally[r.verdict] || 0) + 1; });
  console.log('\n' + '='.repeat(64));
  console.log(`判定：${JSON.stringify(tally)}（共 ${results.length} 条）  pageerror=${pageErrors.length}`);
  fs.mkdirSync(path.dirname(EVID), { recursive: true });
  fs.writeFileSync(EVID, JSON.stringify({ generatedAt: new Date().toISOString(), base: BASE, pageErrors, aggregate: tally, results }, null, 2), 'utf8');
  console.log(`报告：${EVID}`);
  process.exit(0);
})();
