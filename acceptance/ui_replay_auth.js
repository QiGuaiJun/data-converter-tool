/**
 * 登录模块（P1–P4）UI 端到端验证（Playwright）。
 *
 * 覆盖业主明确要求的两条主线：
 *   ① 登录页是**独立页面**，未登录访问任何模块页都会被引导过去（302 带 next）；
 *   ② 登录成功后才能进入主模块，并且登录后按角色控制入口与接口权限。
 *
 * 另外验证 P4 的公网强化：Cookie HttpOnly、写操作必须有 CSRF 校验头、
 * 只读账号执行写语句被 403、跨角色访问管理员接口被 403、Basic 通道未被破坏。
 *
 * 自带隔离实例（端口 51988，DATA_DIR 指向 acceptance/replay-auth-ui），
 * 不触碰 runtime/ 生产数据。用法：
 *   NODE_PATH=... node acceptance/ui_replay_auth.js
 */
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const PROJECT = path.resolve(__dirname, '..');
const PORT = Number(process.env.PORT_AUTH_UI || 51988);
const BASE = `http://127.0.0.1:${PORT}`;
const SANDBOX = path.resolve(__dirname, process.env.DC_REPLAY_SANDBOX_AUTH_UI || 'replay-auth-ui');
const EVID = path.resolve(__dirname, 'evidence/20260922/auth-ui.json');
const ADMIN_USER = 'admin';
const ADMIN_PWD = 'Auth-Ui-Admin-2026';
const VIEWER_USER = 'ui_viewer';
const VIEWER_PWD = 'Ui-Viewer-2026';

const results = [];
const pageErrors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function rec(id, verdict, detail) {
  results.push({ id, verdict, detail: String(detail).slice(0, 900) });
  console.log(`[${verdict.padEnd(13)}] ${id} :: ${String(detail).slice(0, 190)}`);
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
      APP_AUTH_ENABLED: 'true',
      ADMIN_USER: ADMIN_USER,
      ADMIN_PASSWORD: ADMIN_PWD,
      HOST: '127.0.0.1',
      PORT: String(PORT),
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
    },
    stdio: ['ignore', fs.openSync(path.join(SANDBOX, 'server.log'), 'w'), 'inherit'],
  });
}

async function waitReady(timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(BASE + '/api/ping');
      if (r.ok) return true;
    } catch (e) {
      /* 继续等 */
    }
    await sleep(700);
  }
  return false;
}

function attach(page) {
  page.on('pageerror', (error) => pageErrors.push(String(error.message || error).slice(0, 200)));
  page.on('dialog', (dialog) => dialog.accept());
  return page;
}

async function fillLogin(page, username, password) {
  await page.fill('#loginUsername', username);
  await page.fill('#loginPassword', password);
  await page.click('#loginSubmit');
}

/** 用 XMLHttpRequest 发一个「不带自定义头」的写请求，模拟 CSRF 场景。 */
function rawWriteProbe(page, url, body) {
  return page.evaluate(
    ([target, payload]) =>
      new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', target);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText.slice(0, 220) });
        xhr.onerror = () => resolve({ status: 0, body: 'network error' });
        xhr.send(JSON.stringify(payload));
      }),
    [url, body]
  );
}

async function main() {
  const child = startServer();
  const ready = await waitReady();
  if (!ready) {
    rec('BOOT', 'FAIL', '隔离实例未在 60 秒内就绪');
    child.kill();
    process.exit(1);
  }

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = attach(await context.newPage());

  try {
    // ---------------------------------------------------------- 01 未登录拦截
    await page.goto(BASE + '/query.html', { waitUntil: 'domcontentloaded' });
    const guardUrl = new URL(page.url());
    const guardOk = guardUrl.pathname === '/login.html' && guardUrl.searchParams.get('next') === '/query.html';
    rec('AUTH-01', guardOk ? 'PASS' : 'FAIL', `未登录访问 /query.html → ${guardUrl.pathname}${guardUrl.search}`);

    // ---------------------------------------------------------- 02 登录页独立
    const loginShape = await page.evaluate(() => ({
      hasForm: Boolean(document.querySelector('#loginForm')),
      shellLoaded: Array.from(document.scripts).some((s) => (s.src || '').indexOf('shell.js') >= 0),
      userBox: Boolean(document.querySelector('.auth-user-box')),
    }));
    rec(
      'AUTH-02',
      loginShape.hasForm && !loginShape.shellLoaded && !loginShape.userBox ? 'PASS' : 'FAIL',
      `登录页独立：表单=${loginShape.hasForm} shell.js=${loginShape.shellLoaded} 用户区=${loginShape.userBox}`
    );

    // ---------------------------------------------------------- 03 错误口令
    await fillLogin(page, ADMIN_USER, 'definitely-wrong');
    await sleep(1000);
    const wrongState = await page.evaluate(() => ({
      message: ((document.querySelector('#loginMessage') || {}).textContent || '').trim(),
      path: window.location.pathname,
    }));
    rec(
      'AUTH-03',
      wrongState.path === '/login.html' && wrongState.message.length > 0 ? 'PASS' : 'FAIL',
      `错误口令：仍停留=${wrongState.path}，提示="${wrongState.message}"`
    );

    // ---------------------------------------------------------- 04 正确口令 + 回跳 next
    await page.goto(BASE + '/login.html?next=' + encodeURIComponent('/query.html'), {
      waitUntil: 'domcontentloaded',
    });
    await fillLogin(page, ADMIN_USER, ADMIN_PWD);
    await page.waitForURL('**/query.html', { timeout: 15000 }).catch(() => {});
    await page.waitForSelector('.auth-user-name', { timeout: 10000 }).catch(() => {});
    const afterLogin = await page.evaluate(() => ({
      path: window.location.pathname,
      name: (document.querySelector('.auth-user-name') || {}).textContent || '',
      role: (document.querySelector('.auth-user-role') || {}).textContent || '',
    }));
    rec(
      'AUTH-04',
      afterLogin.path === '/query.html' && afterLogin.name === ADMIN_USER ? 'PASS' : 'FAIL',
      `登录后回到 ${afterLogin.path}，用户区 "${afterLogin.name} / ${afterLogin.role}"`
    );

    // ---------------------------------------------------------- 05 Cookie 不可被脚本读取
    const cookieState = await page.evaluate(() => ({
      visible: document.cookie.indexOf('dc_session') >= 0,
      keys: Object.keys(window.localStorage).filter((k) => k.toLowerCase().indexOf('session') >= 0).length,
    }));
    rec(
      'AUTH-05',
      !cookieState.visible && cookieState.keys === 0 ? 'PASS' : 'FAIL',
      `document.cookie 读不到 dc_session=${cookieState.visible}；localStorage 会话键=${cookieState.keys}`
    );

    // ---------------------------------------------------------- 06 管理员入口与账号列表
    await page.goto(BASE + '/users.html', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#userRows tr', { timeout: 15000 }).catch(() => {});
    const usersView = await page.evaluate(() => ({
      navAdmin: Boolean(document.querySelector('.module-tree a[href="/users.html"]')),
      navAudit: Boolean(document.querySelector('.module-tree a[href="/audit.html"]')),
      rows: document.querySelectorAll('#userRows tr').length,
      text: (document.querySelector('#userRows') || {}).textContent || '',
    }));
    rec(
      'AUTH-06',
      usersView.navAdmin && usersView.navAudit && usersView.text.indexOf(ADMIN_USER) >= 0 ? 'PASS' : 'FAIL',
      `管理员可见账号管理/审计入口，账号行数=${usersView.rows}`
    );

    // ---------------------------------------------------------- 07 CSRF：管理员身份下仍拦
    const csrfProbe = await rawWriteProbe(page, '/api/queries', {
      name: 'csrf-probe',
      sql: 'select 1',
      connectionId: '',
    });
    rec(
      'AUTH-07',
      csrfProbe.status === 403 && csrfProbe.body.indexOf('X-DC-Request') >= 0 ? 'PASS' : 'FAIL',
      `管理员会话下不带校验头的写请求 → HTTP ${csrfProbe.status} ${csrfProbe.body}`
    );

    // ---------------------------------------------------------- 08 界面新建只读账号
    await page.click('#newUser');
    await page.fill('#dcNewUserName', VIEWER_USER);
    await page.fill('#dcNewUserPassword', VIEWER_PWD);
    await page.selectOption('#dcNewUserRole', 'viewer');
    await page.click('#dcUserCreateOk');
    await sleep(1600);
    const created = await page.evaluate(() => (document.querySelector('#userRows') || {}).textContent || '');
    rec('AUTH-08', created.indexOf(VIEWER_USER) >= 0 ? 'PASS' : 'FAIL', `界面新建只读账号后列表可见=${created.indexOf(VIEWER_USER) >= 0}`);

    // ---------------------------------------------------------- 09 审计页留痕
    await page.goto(BASE + '/audit.html', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#auditRows tr', { timeout: 15000 }).catch(() => {});
    const auditView = await page.evaluate(() => ({
      text: (document.querySelector('#auditRows') || {}).textContent || '',
      retention: (document.querySelector('#auditRetention') || {}).textContent || '',
    }));
    const auditOk = auditView.text.indexOf('login') >= 0 || auditView.text.indexOf('登录') >= 0;
    rec('AUTH-09', auditOk ? 'PASS' : 'FAIL', `审计页留痕=${auditOk}，保留期提示="${auditView.retention}"`);

    // ---------------------------------------------------------- 10 登出
    await page.goto(BASE + '/tables.html', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.auth-logout', { timeout: 15000 });
    await page.click('.auth-logout');
    await page.waitForSelector('#dcConfirmOk', { timeout: 8000 });
    await page.click('#dcConfirmOk');
    await page.waitForURL('**/login.html**', { timeout: 15000 }).catch(() => {});
    rec('AUTH-10', new URL(page.url()).pathname === '/login.html' ? 'PASS' : 'FAIL', `登出后落在 ${new URL(page.url()).pathname}`);

    await page.goto(BASE + '/tables.html', { waitUntil: 'domcontentloaded' });
    const blockedAgain = new URL(page.url()).pathname === '/login.html';
    rec('AUTH-11', blockedAgain ? 'PASS' : 'FAIL', `登出后会话确实失效，再次访问被拦=${blockedAgain}`);

    // ---------------------------------------------------------- 12 只读账号登陆后的行为
    await page.goto(BASE + '/login.html', { waitUntil: 'domcontentloaded' });
    await fillLogin(page, VIEWER_USER, VIEWER_PWD);
    await sleep(1500);
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await sleep(1800);
    rec(
      'AUTH-12',
      new URL(page.url()).pathname === '/tables.html' ? 'PASS' : 'FAIL',
      `只读账号访问导入页被引导到 ${new URL(page.url()).pathname}`
    );

    const navState = await page.evaluate(() => {
      const wanted = ['/', '/export.html', '/jobs.html', '/schedule.html', '/connections.html'];
      const hidden = [];
      document.querySelectorAll('.module-tree a[href]').forEach((node) => {
        if (wanted.indexOf(node.getAttribute('href')) >= 0 && getComputedStyle(node).display === 'none') {
          hidden.push(node.getAttribute('href'));
        }
      });
      return { hidden, adminEntry: Boolean(document.querySelector('.module-tree a[href="/users.html"]')) };
    });
    rec(
      'AUTH-13',
      navState.hidden.length >= 5 && !navState.adminEntry ? 'PASS' : 'FAIL',
      `只读账号隐藏入口=${navState.hidden.join(',')}；管理员入口=${navState.adminEntry}`
    );

    await page.goto(BASE + '/users.html', { waitUntil: 'domcontentloaded' });
    await sleep(1800);
    rec(
      'AUTH-14',
      new URL(page.url()).pathname === '/tables.html' ? 'PASS' : 'FAIL',
      `只读账号访问账号管理页被引导到 ${new URL(page.url()).pathname}`
    );

    // ---------------------------------------------------------- 15 只读账号的写语句拦截
    await page.goto(BASE + '/tables.html', { waitUntil: 'domcontentloaded' });
    await sleep(900);
    const writeAttempt = await page.evaluate(async () => {
      const response = await fetch('/api/query/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targetDbType: 'sqlite', connectionId: '', sql: 'drop table auth_ui_probe' }),
      });
      return { status: response.status, body: await response.json() };
    });
    rec(
      'AUTH-15',
      writeAttempt.status === 403 && (writeAttempt.body.blockedStatements || []).length === 1 ? 'PASS' : 'FAIL',
      `只读账号执行 DROP → HTTP ${writeAttempt.status}，拦截 ${(writeAttempt.body.blockedStatements || []).length} 条`
    );

    const readAttempt = await page.evaluate(async () => {
      const response = await fetch('/api/query/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targetDbType: 'sqlite', connectionId: '', sql: 'select 1 as value' }),
      });
      return { status: response.status, body: await response.json() };
    });
    rec(
      'AUTH-16',
      readAttempt.status === 200 ? 'PASS' : 'FAIL',
      `只读账号执行 SELECT → HTTP ${readAttempt.status}，行数=${readAttempt.body.rowCount}`
    );

    const adminProbe = await page.evaluate(async () => {
      const response = await fetch('/api/audit-logs?limit=1');
      return { status: response.status, body: await response.json() };
    });
    rec(
      'AUTH-17',
      adminProbe.status === 403 ? 'PASS' : 'FAIL',
      `只读账号访问审计接口 → HTTP ${adminProbe.status}（needRole=${adminProbe.body.needRole}）`
    );

    // ---------------------------------------------------------- 18 脚本通道未被破坏
    const basic = Buffer.from(`${ADMIN_USER}:${ADMIN_PWD}`).toString('base64');
    const basicResponse = await fetch(BASE + '/api/connections', { headers: { Authorization: `Basic ${basic}` } });
    rec('AUTH-18', basicResponse.status === 200 ? 'PASS' : 'FAIL', `HTTP Basic 通道（脚本 / 健康检查）→ HTTP ${basicResponse.status}`);

    const anonResponse = await fetch(BASE + '/api/connections');
    rec('AUTH-19', anonResponse.status === 401 ? 'PASS' : 'FAIL', `匿名访问接口 → HTTP ${anonResponse.status}`);

    rec('AUTH-20', pageErrors.length === 0 ? 'PASS' : 'FAIL', `页面 JS 异常 ${pageErrors.length} 条 ${pageErrors.join(' | ')}`);
  } catch (error) {
    rec('RUNNER', 'FAIL', `执行异常：${error && error.message}`);
  } finally {
    await browser.close();
    child.kill();
  }

  const summary = {
    generatedAt: new Date().toISOString().slice(0, 19),
    base: BASE,
    sandbox: SANDBOX,
    counts: results.reduce((acc, item) => {
      acc[item.verdict] = (acc[item.verdict] || 0) + 1;
      return acc;
    }, {}),
    pageErrors,
    results,
  };
  fs.mkdirSync(path.dirname(EVID), { recursive: true });
  fs.writeFileSync(EVID, JSON.stringify(summary, null, 2), 'utf8');
  console.log(`\n结论：${JSON.stringify(summary.counts)}　证据：${EVID}`);
  process.exit(results.some((item) => item.verdict !== 'PASS') ? 1 : 0);
}

main();
