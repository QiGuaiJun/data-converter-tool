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
      // 让服务端认为"邮箱自助找回已启用"，这样登录页会渲染两步表单。
      // 这里**不需要真能发信**：邮件happy path 用 Playwright 的 route 拦截伪造响应，
      // 真实发信逻辑由 tests/test_p7_login_alias_and_recovery.py 覆盖。
      DC_SMTP_HOST: "smtp.invalid.test",
      DC_SMTP_PORT: "465",
      DC_SMTP_USER: "noreply@invalid.test",
      DC_SMTP_PASSWORD: "unit-test-only",
      DC_SMTP_FROM: "noreply@invalid.test",
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
      // 2026-09-30 起多了「邮箱 / 手机」列（登录别名 + 自助找回渠道），故列数为 8
      const hasContact = headers.includes("邮箱") && headers.includes("手机");
      const newUserListed = (await c.page.locator("#userRows").innerText()).includes(NEW_USER);
      rec(
        "AUTH-18",
        hasName && !hasDisplay && hasContact && rows >= 2 && cols === 8 && newUserListed ? "PASS" : "FAIL",
        `表头「${headers}」行数=${rows} 列数=${cols} 含邮箱手机列=${hasContact} 含新注册账号=${newUserListed}`
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
    /* ---------------------------------------------------- 9 忘记密码入口
       编号接在原有 20 条之后，避免重排既有用例号。 */
    await guard("AUTH-21", async () => {
      await b.page.goto(`${BASE}/login.html`, { waitUntil: "domcontentloaded" });
      await b.page.waitForTimeout(700);
      const label = (await b.page.locator("#forgotToggle").innerText()).trim();
      const closedByDefault = await b.page.locator("#forgotPanel").isHidden();
      await b.page.click("#forgotToggle");
      await b.page.waitForTimeout(250);
      const opened = await b.page.locator("#forgotPanel").isVisible();
      const expanded = await b.page.getAttribute("#forgotToggle", "aria-expanded");
      // 两种形态：配了发信邮箱走两步自助表单，没配则显示"联系管理员"的兜底说明
      const emailMode = await b.page.evaluate(
        () => !document.querySelector("#forgotForm").classList.contains("ln-hidden")
      );
      const hint = (await b.page.locator("#forgotHint").innerText()).trim();
      const sendLabel = (await b.page.locator("#forgotSendBtn").innerText()).trim();
      const fallbackVisible = await b.page.locator("#forgotFallback").isVisible();
      const contact = (await b.page.locator("#forgotContact").innerText()).trim();
      await b.page.click("#forgotToggle");
      await b.page.waitForTimeout(250);
      const collapsed = await b.page.locator("#forgotPanel").isHidden();
      const contentOk = emailMode ? sendLabel.length > 0 && !fallbackVisible : hint.includes("管理员");
      const ok = label.length > 0 && closedByDefault && opened && collapsed && expanded === "true" && contentOk;
      rec(
        "AUTH-21",
        ok ? "PASS" : "FAIL",
        `「${label}」默认收起=${closedByDefault} 点开显示=${opened} 再点收起=${collapsed} ` +
          `aria-expanded=${expanded} 邮箱自助模式=${emailMode} 内容符合该模式=${contentOk} 联系方式行「${contact}」`
      );
    });

    // 切到注册模式时入口应收起（注册页没有"忘记密码"这回事），回到登录模式再出现
    await guard("AUTH-22", async () => {
      await b.page.click("#signupToggle");
      await b.page.waitForTimeout(300);
      const hiddenInSignup = await b.page.locator("#forgotRow").isHidden();
      await b.page.click("#signupToggle");
      await b.page.waitForTimeout(300);
      const visibleInLogin = await b.page.locator("#forgotRow").isVisible();
      rec(
        "AUTH-22",
        hiddenInSignup && visibleInLogin ? "PASS" : "FAIL",
        `注册模式隐藏=${hiddenInSignup} 回到登录模式显示=${visibleInLogin}`
      );
    });

    /* ------------------------------------------- 10 邮箱验证码自助找回
       邮件 happy path 用 route 拦截伪造响应：本脚本起的服务并没有真的发信能力，
       真实发信与验证码校验逻辑由 tests/test_p7_login_alias_and_recovery.py 覆盖。 */
    await guard("AUTH-23", async () => {
      const page = b.page;
      const trace = [];
      const step = async (name, fn) => {
        trace.push(name);
        return fn();
      };
      try {
        await step("goto", async () => {
          await page.goto(`${BASE}/login.html`, { waitUntil: "domcontentloaded" });
          await page.waitForTimeout(500);
        });
        await step("route", async () => {
          await page.route(/\/api\/auth\/forgot\/send$/, (route) =>
            route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify({
                ok: true, sent: true, target: "z******n@corp.com", ttlMinutes: 10,
                message: "验证码已发送至 z******n@corp.com，10 分钟内有效。",
              }),
            })
          );
          await page.route(/\/api\/auth\/forgot\/reset$/, (route) =>
            route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify({ ok: true, message: "密码已重置，请用新密码登录。" }),
            })
          );
        });
        await step("open-panel", () => page.click("#forgotToggle"));
        await page.waitForTimeout(250);
        const step1Visible = await page.locator("#forgotStep1").isVisible();
        const step2HiddenFirst = await page.locator("#forgotStep2").isHidden();

        await step("fill-identifier", () => page.fill("#forgotIdentifier", "zhangsan@corp.com"));
        await step("click-send", () => page.click("#forgotSendBtn"));
        await page.waitForTimeout(600);
        const step2Visible = await page.locator("#forgotStep2").isVisible();
        const tipAfterSend = (await page.locator("#forgotTip").innerText()).trim();

        await step("fill-code", () => page.fill("#forgotCode", "123456"));
        await step("fill-password", () => page.fill("#forgotPassword", "Brand-New-2026"));
        await step("fill-confirm", () => page.fill("#forgotPassword2", "Brand-New-2026"));
        // 点之前先探一下按钮的真实状态（disabled / 被遮挡 / 尺寸为 0），
        // 否则 Playwright 只会给一句"等待元素可见可点"，很难定位。
        const probe = await page.evaluate(() => {
          const btn = document.querySelector("#forgotResetBtn");
          const rect = btn.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
          return {
            disabled: btn.disabled,
            step2Hidden: document.querySelector("#forgotStep2").classList.contains("ln-hidden"),
            rect: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)],
            hit: hit ? (hit.id || hit.className || hit.tagName) : "(null)",
          };
        });
        trace.push("probe=" + JSON.stringify(probe));
        await step("click-reset", () => page.click("#forgotResetBtn"));
        await page.waitForTimeout(700);
        const panelHidden = await page.locator("#forgotPanel").isHidden();
        const loginMessage = (await page.locator("#loginMessage").innerText()).trim();
        const filledUser = await page.inputValue("#loginUsername");

        await page.unroute(/\/api\/auth\/forgot\/send$/).catch(() => {});
        await page.unroute(/\/api\/auth\/forgot\/reset$/).catch(() => {});

        const ok =
          step1Visible && step2HiddenFirst && step2Visible &&
          tipAfterSend.includes("z******n@corp.com") &&
          panelHidden && loginMessage.includes("已重置") && filledUser === "zhangsan@corp.com";
        rec(
          "AUTH-23",
          ok ? "PASS" : "FAIL",
          `第一步可见=${step1Visible} 第二步初始隐藏=${step2HiddenFirst} 发送后出现第二步=${step2Visible} ` +
            `提示「${tipAfterSend}」→ 重置后面板收起=${panelHidden} 登录页提示「${loginMessage}」回填账号=${filledUser}`
        );
      } catch (error) {
        await page.unroute(/\/api\/auth\/forgot\/send$/).catch(() => {});
        await page.unroute(/\/api\/auth\/forgot\/reset$/).catch(() => {});
        rec(
          "AUTH-23",
          "FAIL",
          `中断于步骤「${trace[trace.length - 1]}」URL=${page.url()}｜步骤轨迹 ${trace.join(" → ")}｜` +
            String(error.message).replace(/\s+/g, " ").slice(0, 240)
        );
      }
    });

    // 服务端未启用邮箱找回时，应退回"联系管理员"的静态说明（不显示两步表单）
    await guard("AUTH-24", async () => {
      const page = b.page;
      await page.route(/\/api\/auth\/signup-info/, (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true, authEnabled: true, signupCodeRequired: false, userCount: 3,
            appVersion: "0.0.0-test", emailRecovery: false,
            defaultRole: "viewer", defaultRoleLabel: "只读", defaultRoleHint: "测试用",
          }),
        })
      );
      await page.goto(`${BASE}/login.html`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(700);
      await page.click("#forgotToggle");
      await page.waitForTimeout(250);
      const fallbackVisible = await page.locator("#forgotFallback").isVisible();
      const formHidden = await page.locator("#forgotForm").isHidden();
      const hint = (await page.locator("#forgotHint").innerText()).trim();
      await page.unroute(/\/api\/auth\/signup-info/);
      const ok = fallbackVisible && formHidden && hint.includes("管理员");
      rec(
        "AUTH-24",
        ok ? "PASS" : "FAIL",
        `emailRecovery=false → 兜底说明可见=${fallbackVisible} 两步表单隐藏=${formHidden} 文案提及管理员=${hint.includes("管理员")}`
      );
    });

    /* --------------------------------- 11 账号管理：邮箱 / 手机（自助找回的前提） */
    await guard("AUTH-25", async () => {
      const page = c.page;
      await page.goto(`${BASE}/users.html`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(800);
      const headers = await page.locator(".user-table thead th").allInnerTexts();
      const hasColumn = headers.some((text) => text.replace(/\s/g, "").includes("邮箱") && text.includes("手机"));
      const contactCells = await page.locator("tbody .user-contact").count();
      const contactButton = await page.locator('button[data-action="contact"]').count();
      const before = (await page.locator("tbody .user-contact").first().innerText()).trim();

      // 点「邮箱/手机」会连弹两个输入框：先邮箱、再手机号。
      // 注意用**一个**监听器按顺序作答 —— 注册两个 page.once 的话，同一个对话框
      // 会同时触发两个回调，第二个会报 "dialog is already handled"。
      const answers = ["zhangsan@corp.com", "13800138000"];
      const onDialog = (dialog) => {
        const value = answers.length ? answers.shift() : "";
        dialog.accept(value).catch(() => {});
      };
      page.on("dialog", onDialog);
      await page.click('button[data-action="contact"]');
      await page.waitForTimeout(1500);
      page.off("dialog", onDialog);
      const after = (await page.locator("tbody .user-contact").first().innerText()).trim();

      const ok =
        hasColumn && contactCells > 0 && contactButton > 0 &&
        after.includes("zhangsan@corp.com") && after.includes("13800138000");
      rec(
        "AUTH-25",
        ok ? "PASS" : "FAIL",
        `表头含「邮箱/手机」=${hasColumn} 数据行数=${contactCells} 操作按钮=${contactButton} ` +
          `绑定前「${before}」→ 绑定后「${after.replace(/\s+/g, " ")}」`
      );
    });

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
