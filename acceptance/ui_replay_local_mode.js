/* 本机模式（未启用认证）UI 回归验证
 *
 * 背景（2026-09-23 实测到的回归）：
 *   `handle_auth_me` 原先只看会话，未启用认证时既无会话也无 auth_user → 返回 401；
 *   前端 shell.js 见 401 就跳 `/login.html`，而登录页在未启用认证时**没有注册入口、
 *   也登不进来**（signup-info 的 authEnabled=false）→ 本机桌面版整站被弹到一个进不去的
 *   登录页。本机版是用户日常主力用法，所以单独立一条回归用例守住它。
 *
 * 自带隔离服务：APP_AUTH_ENABLED=false（且不设 ADMIN_PASSWORD），端口 51997。
 * 绝不碰 runtime/ 生产数据。
 *
 * 运行：
 *   NODE_PATH=<...>/node_modules node acceptance/ui_replay_local_mode.js
 *   （前缀 PYTHONPATH= CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0）
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const net = require("net");

const ROOT = path.resolve(__dirname, "..");
const PORT = Number(process.env.PORT_LOCAL_MODE || 51997);
const BASE = `http://127.0.0.1:${PORT}`;
const EVIDENCE_DIR = path.join(ROOT, "acceptance", "evidence", "20260923");
const EVIDENCE_FILE = path.join(EVIDENCE_DIR, "local-mode-ui.json");

const PAGES = ["/", "/index.html", "/query.html", "/jobs.html", "/tables.html", "/export.html"];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function resolveSandbox() {
  const base = process.env.DC_REPLAY_SANDBOX_LOCAL || "replay-local-mode";
  const parent = path.join(ROOT, "acceptance");
  let candidate = path.join(parent, base);
  let index = 1;
  while (fs.existsSync(candidate)) {
    candidate = path.join(parent, `${base}-run${++index}`);
  }
  fs.mkdirSync(candidate, { recursive: true });
  return candidate;
}

function waitPort(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const socket = net.connect({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() > deadline) reject(new Error(`端口 ${port} 未就绪`));
        else setTimeout(attempt, 400);
      });
    };
    attempt();
  });
}

const results = [];
const pageErrors = [];
function rec(id, verdict, detail) {
  results.push({ id, verdict, detail: String(detail || "") });
  console.log(`[${verdict}] ${id} :: ${detail}`);
}

async function main() {
  const sandbox = resolveSandbox();
  console.log(`沙箱：${path.relative(ROOT, sandbox)}  端口：${PORT}（APP_AUTH_ENABLED=false）`);

  const env = {
    ...process.env,
    HOST: "127.0.0.1",
    PORT: String(PORT),
    DATA_DIR: path.join(sandbox, "data"),
    UPLOADS_DIR: path.join(sandbox, "uploads"),
    EXPORTS_DIR: path.join(sandbox, "exports"),
    APP_AUTH_ENABLED: "false",
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
  // 本机模式不应该存在管理员口令；显式清掉，避免污染判定
  delete env.ADMIN_PASSWORD;
  delete env.ADMIN_USER;

  const child = spawn(path.join(ROOT, ".venv", "Scripts", "python.exe"), ["server.py"], {
    cwd: ROOT,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const serverLog = [];
  child.stdout.on("data", (chunk) => serverLog.push(String(chunk)));
  child.stderr.on("data", (chunk) => serverLog.push(String(chunk)));

  let browser = null;
  try {
    await waitPort(PORT, 25000);

    // 服务端契约：本机模式下 /api/auth/me 必须 200 且给出本机身份
    const meta = await (await fetch(`${BASE}/api/meta`)).json();
    const meResponse = await fetch(`${BASE}/api/auth/me`);
    const me = await meResponse.json().catch(() => ({}));
    rec(
      "LOCAL-01",
      meResponse.status === 200 && me.authEnabled === false && (me.user || {}).channel === "local"
        ? "PASS"
        : "FAIL",
      `appVersion=${meta.appVersion} /api/auth/me → HTTP ${meResponse.status} authEnabled=${me.authEnabled} channel=${
        (me.user || {}).channel
      }（期望 200 / false / local）`
    );

    const { chromium } = require("playwright");
    browser = await chromium.launch();
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on("pageerror", (error) => pageErrors.push(String(error && error.message)));

    for (const [index, target] of PAGES.entries()) {
      const id = `LOCAL-${String(index + 2).padStart(2, "0")}`;
      try {
        await page.goto(BASE + target, { waitUntil: "domcontentloaded" });
        await page.waitForTimeout(900);
        const url = page.url().replace(BASE, "");
        const bounced = url.includes("/login.html");
        const loginForm = await page.locator("#loginForm").count();
        const userBox = await page.locator(".auth-user-box").count();
        const nav = await page.locator(".module-tree a[href]").count();
        const ok = !bounced && loginForm === 0 && userBox === 0 && nav > 0;
        rec(
          id,
          ok ? "PASS" : "FAIL",
          `${target} → ${url}｜被弹登录页=${bounced} 出现登录表单=${loginForm} 用户区=${userBox} 侧边栏入口=${nav}`
        );
      } catch (error) {
        rec(id, "FAIL", `${target} 异常：${error && error.message ? error.message : error}`);
      }
    }

    rec("LOCAL-08", pageErrors.length === 0 ? "PASS" : "FAIL", `页面 JS 异常 ${pageErrors.length} 条 ${pageErrors.join(" | ")}`);
    await context.close();
  } catch (error) {
    rec("RUN", "FAIL", `执行中断：${error && error.message ? error.message : error}`);
  } finally {
    if (browser) await browser.close().catch(() => {});
    child.kill();
    await sleep(500);
    fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
    fs.writeFileSync(
      EVIDENCE_FILE,
      JSON.stringify(
        {
          generatedAt: new Date().toISOString(),
          base: BASE,
          sandbox: path.relative(ROOT, sandbox),
          mode: "APP_AUTH_ENABLED=false（本机桌面模式）",
          counts: results.reduce((acc, item) => {
            acc[item.verdict] = (acc[item.verdict] || 0) + 1;
            return acc;
          }, {}),
          pageErrors,
          results,
          serverLog: serverLog.join("").split(/\r?\n/).slice(-30),
        },
        null,
        2
      ),
      "utf8"
    );
    const pass = results.filter((item) => item.verdict === "PASS").length;
    const fail = results.filter((item) => item.verdict === "FAIL").length;
    console.log(`\n结论：PASS ${pass} / FAIL ${fail} / 共 ${results.length} 条`);
    console.log(`证据：${path.relative(ROOT, EVIDENCE_FILE)}`);
  }
}

main();
