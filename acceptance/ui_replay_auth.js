/* 登录页 + 登录模块 UI 端到端验证（2026-09-22 改版后）
 *
 * 自带隔离服务与夹具：不依赖外部已启动的服务，也绝不碰 runtime/ 生产数据。
 *   · 沙箱目录 acceptance/<DC_REPLAY_SANDBOX_AUTH_UI>[-runN]
 *   · 端口 PORT_AUTH_UI（默认 51990）
 *   · 服务端以 APP_AUTH_ENABLED=true 启动，管理员口令为**本脚本内的测试口令**
 *
 * 覆盖点随 2026-09-22 的功能调整更新：
 *   · 账号名称允许任意字符（中文/空格/符号），且账号名即显示名
 *   · 「记住我」已下线、注册表单不再有「显示名」
 *   · 密码支持切换明文显示
 *   · 登录页为独立页面，未登录一律跳转登录页
 *
 * 运行：
 *   NODE_PATH=<...>/node_modules node acceptance/ui_replay_auth.js
 *   （前缀 PYTHONPATH= CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0）
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const net = require("net");

const ROOT = path.resolve(__dirname, "..");
const PORT = Number(process.env.PORT_AUTH_UI || 51990);
const BASE = `http://127.0.0.1:${PORT}`;

// 仅用于本脚本起的隔离服务，不是任何真实环境的凭据
const ADMIN_USER = "admin";
const ADMIN_PASSWORD = "AuthUi-Test-2026";
const NEW_USER = "测试 账号·A1";
const NEW_PASSWORD = "NewUserPass!2026";

const EVIDENCE_DIR = path.join(ROOT, "acceptance", "evidence", "20260922");
const EVIDENCE_FILE = path.join(EVIDENCE_DIR, "auth-ui.json");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* ------------------------------------------------------------------ 沙箱 */
function resolveSandbox() {
  const base = process.env.DC_REPLAY_SANDBOX_AUTH_UI || "replay-auth-ui";
  const parent = path.join(ROOT, "acceptance");
  let candidate = path.join(parent, base);
  let index = 1;
  // 不删旧目录：存在就换一个新的 -runN，避免误删上一轮证据
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
        if (Date.now() > deadline) {
          reject(new Error(`端口 ${port} 在 ${timeoutMs}ms 内未就绪`));
        } else {
          setTimeout(attempt, 400);
        }
      });
    };
    attempt();
  });
}

/* ------------------------------------------------------------------ 结果 */
const results = [];
const pageErrors = [];

function rec(id, verdict, detail) {
  results.push({ id, verdict, detail: String(detail || "") });
  const mark = verdict === "PASS" ? "PASS" : verdict === "FAIL" ? "FAIL" : "MANUAL";
  console.log(`[${mark}] ${id} :: ${detail}`);
}

async function guard(id, fn) {
  try {
    await fn();
  } catch (error) {
    rec(id, "FAIL", `异常：${error && error.message ? error.message : error}`);
  }
}

async function main() {
  const sandbox = resolveSandbox();
  console.log(`沙箱：${path.relative(ROOT, sandbox)}  端口：${PORT}`);

  const child = spawn(path.join(ROOT, ".venv", "Scripts", "python.exe"), ["server.py"], {
    cwd: ROOT,
    env: {
      ...process.env,
      HOST: "127.0.0.1",
      PORT: String(PORT),
      DATA_DIR: path.join(sandbox, "data"),
      UPLOADS_DIR: path.join(sandbox, "uploads"),
      EXPORTS_DIR: path.join(sandbox, "exports"),
      APP_AUTH_ENABLED: "true",
      ADMIN_USER,
      ADMIN_PASSWORD,
      DC_DEFAULT_ROLE: "operator",
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const serverLog = [];
  child.stdout.on("data", (chunk) => serverLog.push(String(chunk)));
  child.stderr.on("data", (chunk) => serverLog.push(String(chunk)));

  let browser = null;
  try {
    await waitPort(PORT, 25000);

    const { chromium } = require("playwright");
    browser = await chromium.launch();

    const newPage = async () => {
      const context = await browser.newContext({ ignoreHTTPSErrors: true });
      const page = await context.newPage();
      page.on("pageerror", (error) => pageErrors.push(String(error && error.message)));
      return { context, page };
    };

    const login = async (page, username, password) => {
      await page.fill("#loginUsername", username);
      await page.fill("#loginPassword", password);
      await page.click("#loginSubmit");
    };

    const atLoginPage = async (page) => page.url().includes("/login.html");

    /* ---------------------------------------------------- 1 未登录跳转与页面 */
    const a = await newPage();
    await guard("AUTH-01", async () => {
      await a.page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
      await a.page.waitForLoadState("networkidle").catch(() => {});
      const onLogin = await atLoginPage(a.page);
      const hasForm = await a.page.locator("#loginForm").count();
      rec(
        "AUTH-01",
        onLogin && hasForm === 1 ? "PASS" : "FAIL",
        `未登录访问 / → ${a.page.url()}（应在登录页且带登录表单）`
      );
    });

    await guard("AUTH-02", async () => {
      await a.page.goto(`${BASE}/query.html`, { waitUntil: "domcontentloaded" });
      await a.page.waitForLoadState("networkidle").catch(() => {});
      const url = a.page.url();
      rec(
        "AUTH-02",
        url.includes("/login.html") && url.includes("next=") ? "PASS" : "FAIL",
        `未登录访问 /query.html → ${url}（应跳登录页且保留 next）`
      );
    });

    await guard("AUTH-03", async () => {
      const parts = {
        username: await a.page.locator("#fieldUsername input").count(),
        password: await a.page.locator("#fieldPassword input").count(),
        submit: await a.page.locator("#loginSubmit").count(),
        switch: await a.page.locator("#signupToggle").count(),
        brand: (await a.page.locator("#brandCn").innerText()).trim(),
        logo: await a.page.locator(".ln-card-logo svg").count(),
        illustration: await a.page.locator(".ln-visual .ln-node, .ln-visual circle").count(),
        copyright: (await a.page.locator("#footCopyright").innerText()).trim(),
        version: (await a.page.locator("#footVersion").innerText()).trim(),
        footer: (await a.page.locator("#footVision").innerText()).trim(),
      };
      // 版权年份必须是当前年份（配置里用 {year} 占位，防止页面停留在旧年份）
      const yearOk = parts.copyright.includes(String(new Date().getFullYear()));
      const ok =
        parts.username === 1 && parts.password === 1 && parts.submit === 1 &&
        parts.switch === 1 && parts.logo === 1 && parts.illustration > 0 &&
        parts.brand.length > 0 && parts.footer.length > 0 &&
        yearOk && /^v\d+\.\d+\.\d+$/.test(parts.version);
      rec("AUTH-03", ok ? "PASS" : "FAIL", `登录页元素 ${JSON.stringify(parts)}`);
    });

    await guard("AUTH-04", async () => {
      // 「记住我」按需求下线：页面上不应再有任何相关勾选框
      const body = await a.page.locator("body").innerText();
      const hasRemember = /记住我/.test(body);
      const boxes = await a.page.locator('#loginForm input[type="checkbox"]').count();
      rec(
        "AUTH-04",
        !hasRemember && boxes === 0 ? "PASS" : "FAIL",
        `「记住我」文案存在=${hasRemember} 勾选框数=${boxes}（都应为 0/false）`
      );
    });

    await guard("AUTH-05", async () => {
      const hasDisplay = await a.page.locator("#signupDisplay, #dcNewDisplayName").count();
      await a.page.click("#signupToggle");
      await a.page.waitForTimeout(150);
      const body = await a.page.locator("body").innerText();
      const inputs = await a.page.locator('#loginForm input[type="text"]').count();
      const hasDisplayText = /显示名/.test(body);
      rec(
        "AUTH-05",
        hasDisplay === 0 && !hasDisplayText ? "PASS" : "FAIL",
        `显示名控件=${hasDisplay} 文案出现=${hasDisplayText} 注册模式文本输入框数=${inputs}`
      );
    });

    await guard("AUTH-06", async () => {
      const title = (await a.page.locator("#formTitle").innerText()).trim();
      const submitText = (await a.page.locator("#loginSubmit").innerText()).trim();
      const switchText = (await a.page.locator("#signupToggle").innerText()).trim();
      const ok = title.length > 0 && switchText.length > 0 && submitText.length > 0;
      rec("AUTH-06", ok ? "PASS" : "FAIL", `注册模式文案：标题「${title}」按钮「${submitText}」切换「${switchText}」`);
    });

    await guard("AUTH-07", async () => {
      await a.page.click("#signupToggle");
      await a.page.waitForTimeout(150);
      const submitText = (await a.page.locator("#loginSubmit").innerText()).trim();
      const titleVisible = await a.page.locator("#formTitle").isVisible();
      rec(
        "AUTH-07",
        submitText.length > 0 && !titleVisible ? "PASS" : "FAIL",
        `返回登录后按钮「${submitText}」大标题可见=${titleVisible}（登录模式大标题应为空）`
      );
    });

    /* ------------------------------------------------------ 2 密码明文切换 */
    await guard("AUTH-08", async () => {
      const before = await a.page.getAttribute("#loginPassword", "type");
      await a.page.fill("#loginPassword", "Secret123!");
      await a.page.click("#togglePassword");
      await a.page.waitForTimeout(100);
      const shown = await a.page.getAttribute("#loginPassword", "type");
      const visibleText = await a.page.locator("#loginPassword").inputValue();
      const eyeOffVisible = await a.page.locator("#eyeOff").isVisible();
      const pressed = await a.page.getAttribute("#togglePassword", "aria-pressed");
      const ok = before === "password" && shown === "text" && visibleText === "Secret123!" && eyeOffVisible && pressed === "true";
      rec("AUTH-08", ok ? "PASS" : "FAIL", `默认 type=${before} 点击后 type=${shown} 眼睛图标切换=${eyeOffVisible} aria-pressed=${pressed}`);
    });

    await guard("AUTH-09", async () => {
      await a.page.click("#togglePassword");
      await a.page.waitForTimeout(100);
      const after = await a.page.getAttribute("#loginPassword", "type");
      const eyeOpenVisible = await a.page.locator("#eyeOpen").isVisible();
      rec(
        "AUTH-09",
        after === "password" && eyeOpenVisible ? "PASS" : "FAIL",
        `再点一次 type=${after} 睁眼图标可见=${eyeOpenVisible}`
      );
    });

    /* ---------------------------------------------------------- 3 登录失败 */
    await guard("AUTH-10", async () => {
      await a.page.goto(`${BASE}/login.html`, { waitUntil: "domcontentloaded" });
      await a.page.waitForTimeout(300);
      await login(a.page, ADMIN_USER, "definitely-wrong-password");
      await a.page.waitForTimeout(900);
      const message = (await a.page.locator("#loginMessage").innerText()).trim();
      const stillLogin = await atLoginPage(a.page);
      rec(
        "AUTH-10",
        stillLogin && message.length > 0 ? "PASS" : "FAIL",
        `错误口令 → 停留登录页=${stillLogin} 提示「${message}」`
      );
    });

    /* ---------------------------------------------------------- 4 登录成功 */
    await guard("AUTH-11", async () => {
      await a.page.fill("#loginPassword", ADMIN_PASSWORD);
      await a.page.click("#loginSubmit");
      await a.page.waitForURL((url) => !url.pathname.includes("login.html"), { timeout: 15000 }).catch(() => {});
      await a.page.waitForLoadState("networkidle").catch(() => {});
      const url = a.page.url();
      rec("AUTH-11", !url.includes("login.html") ? "PASS" : "FAIL", `正确口令登录 → ${url}`);
    });

    await guard("AUTH-12", async () => {
      await a.page.waitForSelector(".auth-user-box", { timeout: 10000 }).catch(() => {});
      const box = await a.page.locator(".auth-user-box").count();
      const name = box ? (await a.page.locator(".auth-user-name").innerText()).trim() : "";
      const role = box ? (await a.page.locator(".auth-user-role").innerText()).trim() : "";
      const logout = box ? (await a.page.locator(".auth-logout").count()) : 0;
      rec(
        "AUTH-12",
        box === 1 && name === ADMIN_USER && logout === 1 ? "PASS" : "FAIL",
        `用户区=${
          box
        } 账号名称「${name}」角色「${role}」退出按钮=${logout}（账号名即显示名，应与登录名一致）`
      );
    });

    await guard("AUTH-13", async () => {
      const nav = await a.page.locator('.module-tree a[href="/users.html"]').count();
      const audit = await a.page.locator('.module-tree a[href="/audit.html"]').count();
      rec("AUTH-13", nav === 1 && audit === 1 ? "PASS" : "FAIL", `管理员侧边栏：账号管理=${nav} 审计日志=${audit}`);
    });

    /* ------------------------------------------------ 5 任意字符账号名注册 */
    const b = await newPage();
    await guard("AUTH-14", async () => {
      await b.page.goto(`${BASE}/login.html`, { waitUntil: "domcontentloaded" });
      await b.page.waitForTimeout(300);
      await b.page.click("#signupToggle");
      await b.page.waitForTimeout(150);
      const placeholder = await b.page.getAttribute("#fieldUsername input", "placeholder");
      rec("AUTH-14", Boolean(placeholder) ? "PASS" : "FAIL", `注册页账号输入框提示「${placeholder}」`);
    });

    await guard("AUTH-15", async () => {
      await login(b.page, NEW_USER, NEW_PASSWORD);
      await b.page.waitForURL((url) => !url.pathname.includes("login.html"), { timeout: 15000 }).catch(() => {});
      await b.page.waitForLoadState("networkidle").catch(() => {});
      const url = b.page.url();
      const name = (await b.page.locator(".auth-user-name").innerText().catch(() => "")).trim();
      rec(
        "AUTH-15",
        !url.includes("login.html") && name === NEW_USER ? "PASS" : "FAIL",
        `含中文/空格/符号的账号名「${NEW_USER}」注册并登录 → ${url}，用户区显示「${name}」`
      );
    });

    await guard("AUTH-16", async () => {
      const exportNav = await b.page.locator('.module-tree a[href="/export.html"]').isVisible().catch(() => false);
      const usersNav = await b.page.locator('.module-tree a[href="/users.html"]').count();
      const name = (await b.page.locator(".auth-user-name").innerText().catch(() => "")).trim();
      rec(
        "AUTH-16",
        exportNav && usersNav === 0 ? "PASS" : "FAIL",
        `新注册账号（DC_DEFAULT_ROLE=operator）导出入口可见=${exportNav} 管理入口=${usersNav}（应为 0）显示名「${name}」`
      );
    });

    await guard("AUTH-17", async () => {
      // 退出登录会先弹确认框（防误点），要点「确认继续」才真正登出
      await b.page.click(".auth-logout");
      await b.page.waitForSelector("#dcConfirmDialog:not(.hidden) #dcConfirmOk", { timeout: 8000 });
      await b.page.click("#dcConfirmOk");
      await b.page.waitForURL("**/login.html**", { timeout: 15000 }).catch(() => {});
      await b.page.waitForLoadState("networkidle").catch(() => {});
      const back = await atLoginPage(b.page);
      await b.page.goto(`${BASE}/query.html`, { waitUntil: "domcontentloaded" });
      await b.page.waitForLoadState("networkidle").catch(() => {});
      const blocked = await atLoginPage(b.page);
      rec("AUTH-17", back && blocked ? "PASS" : "FAIL", `退出后回到登录页=${back}，再访问受保护页仍被拦=${blocked}`);
    });

    /* ------------------------------------------- 6 账号管理页（去掉显示名） */
    const c = await newPage();
    await guard("AUTH-18", async () => {
      await c.page.goto(`${BASE}/login.html`, { waitUntil: "domcontentloaded" });
      await c.page.waitForTimeout(300);
      await login(c.page, ADMIN_USER, ADMIN_PASSWORD);
      await c.page.waitForURL((url) => !url.pathname.includes("login.html"), { timeout: 15000 }).catch(() => {});
      await c.page.goto(`${BASE}/users.html`, { waitUntil: "domcontentloaded" });
      await c.page.waitForSelector("#userRows tr", { timeout: 10000 }).catch(() => {});
      const headers = (await c.page.locator(".user-table thead").innerText()).replace(/\s+/g, " ");
      const rows = await c.page.locator("#userRows tr").count();
      const cols = await c.page.locator("#userRows tr:first-child td").count();
      const hasName = headers.includes("账号名称");
      const hasDisplay = headers.includes("显示名");
      const newUserListed = (await c.page.locator("#userRows").innerText()).includes(NEW_USER);
      rec(
        "AUTH-18",
        hasName && !hasDisplay && rows >= 2 && cols === 7 && newUserListed ? "PASS" : "FAIL",
        `表头「${headers}」行数=${rows} 列数=${cols} 含新注册账号=${newUserListed}`
      );
    });

    await guard("AUTH-19", async () => {
      const [download] = await Promise.all([
        c.page.waitForEvent("download", { timeout: 15000 }),
        c.page.click("#exportUsers"),
      ]);
      const fileName = download.suggestedFilename();
      const saved = path.join(EVIDENCE_DIR, fileName);
      await download.saveAs(saved);
      const content = fs.readFileSync(saved, "utf8");
      const firstLine = content.split(/\r?\n/)[0].replace(/^\ufeff/, "");
      const noSecret = !/password_hash|scrypt\$/i.test(content);
      const hasBom = content.charCodeAt(0) === 0xfeff;
      const ok = fileName.endsWith(".csv") && firstLine.includes("账号名称") &&
        !firstLine.includes("显示名") && noSecret && hasBom && content.includes(NEW_USER);
      rec(
        "AUTH-19",
        ok ? "PASS" : "FAIL",
        `导出 ${fileName}：表头「${firstLine}」含新账号=${content.includes(NEW_USER)} 无口令痕迹=${noSecret} BOM=${hasBom}`
      );
      fs.unlinkSync(saved);
    });

    /* ------------------------------------------------------------ 7 页面异常 */
    await guard("AUTH-20", async () => {
      rec(
        "AUTH-20",
        pageErrors.length === 0 ? "PASS" : "FAIL",
        `页面 JS 异常 ${pageErrors.length} 条 ${pageErrors.join(" | ")}`
      );
    });
  } catch (error) {
    rec("RUN", "FAIL", `执行中断：${error && error.message ? error.message : error}`);
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
    child.kill();
    await sleep(600);
    fs.writeFileSync(
      EVIDENCE_FILE,
      JSON.stringify(
        {
          generatedAt: new Date().toISOString(),
          base: BASE,
          sandbox: path.relative(ROOT, resolveSandboxHint()),
          counts: results.reduce((acc, item) => {
            acc[item.verdict] = (acc[item.verdict] || 0) + 1;
            return acc;
          }, {}),
          pageErrors,
          results,
          serverLog: serverLog.join("").split(/\r?\n/).slice(-40),
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

// 证据里记录沙箱名（不重新创建目录）
function resolveSandboxHint() {
  const base = process.env.DC_REPLAY_SANDBOX_AUTH_UI || "replay-auth-ui";
  return path.join(ROOT, "acceptance", base);
}

fs.mkdirSync(EVIDENCE_DIR, { recursive: true });
main();
