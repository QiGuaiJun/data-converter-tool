/**
 * 作业 / 定时任务 · 防重复提交与对话框状态重置 走查（2026-10-15）
 *
 * 背景（验收报告 Q-002 / Q-003）：
 *   sync / export / connections / query 各自都做了「await 期间禁用按钮」，
 *   而 jobs.js / schedule.js **一处都没做** —— `await` 期间按钮照样可点，
 *   连点「保存」会 POST 两次、连点「复制作业」会一次造出多条副本。
 *   同时「取消」关闭对话框后不重置模块级草稿状态（editingJobId / draftSteps），
 *   残留会污染下一次编辑。
 *
 * 本走查不只验"结果"，还做**负向对照**：
 *   把 `window.DCBusy.run` 临时替换成直通实现（等于回到修复前的行为），
 *   再用同样的双击去触发 —— 必须真的发出 2 个请求。
 *   只有"装了守卫=1 次、摘掉守卫=2 次"同时成立，才能证明这个守卫真的在起作用，
 *   而不是因为别的原因（例如按钮本来就点不动）看起来通过了。
 *
 * 跑法：bash scripts/run_jobs_dup_walk.sh
 * 隔离：沙箱 DATA_DIR，不碰 runtime/data/imports.db。
 */
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:51984";
const DELAY_MS = 700; // 把写接口的响应撑开，制造"按钮还来得及被连点"的窗口

const results = [];
function rec(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name} :: ${detail}`);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1680, height: 900 } });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e.message || e)));
  // 删除确认框一律同意；同时记录弹了几次，用于判断有没有重复触发
  let confirms = 0;
  page.on("dialog", (d) => { confirms++; d.accept(); });

  const posts = { jobs: 0, run: 0, schedules: 0 };
  await page.route(/\/api\/(jobs|schedules)/, async (route) => {
    const req = route.request();
    if (req.method() === "POST") {
      const path = new URL(req.url()).pathname;
      if (path === "/api/jobs") posts.jobs++;
      else if (path === "/api/jobs/run") posts.run++;
      else if (path === "/api/schedules") posts.schedules++;
      await new Promise((r) => setTimeout(r, DELAY_MS));
    }
    await route.continue();
  });

  // 摘掉守卫 = 回到修复前的行为，用于负向对照
  const bypassGuard = () => page.evaluate(() => { window.DCBusy = { run: (b, t, action) => action() }; });
  const restoreGuard = () => page.reload({ waitUntil: "domcontentloaded" });

  // ---------------------------------------------------------------- 作业页
  await page.goto(BASE + "/jobs.html", { waitUntil: "networkidle" });
  await page.waitForTimeout(600);

  const jobCount = () => page.$$eval("#jobList .job-item", (els) => els.length);
  const baseJobs = await jobCount();
  rec("J0 作业页加载、沙箱里已有预置作业", baseJobs >= 1, `列表 ${baseJobs} 条`);

  await page.click("#jobList .job-item");
  await page.waitForTimeout(200);

  // ---- A) 复制作业 · 双击 ----
  let before = posts.jobs;
  await page.click("#duplicateJob", { clickCount: 2, delay: 30 });
  await page.waitForTimeout(1800);
  const dupGuarded = posts.jobs - before;
  const afterJobs = await jobCount();
  rec("J1 连点「复制作业」只发 1 个 POST", dupGuarded === 1, `POST ${dupGuarded} 次`);
  rec("J2 连点「复制作业」只造出 1 条副本", afterJobs - baseJobs === 1, `${baseJobs} → ${afterJobs} 条`);

  // ---- A') 负向对照：摘掉守卫，同样双击必须发 2 个 POST ----
  await bypassGuard();
  await page.click("#jobList .job-item");
  await page.waitForTimeout(200);
  before = posts.jobs;
  await page.click("#duplicateJob", { clickCount: 2, delay: 30 });
  await page.waitForTimeout(1800);
  const dupUnguarded = posts.jobs - before;
  rec("J3 负向对照：摘掉守卫后同样双击确实发 2 个 POST", dupUnguarded === 2,
    `POST ${dupUnguarded} 次（证明 J1 不是空转）`);
  await restoreGuard();
  await page.waitForTimeout(800);

  // ---- B) 保存作业 · 双击 ----
  await page.dblclick("#jobList .job-item");
  await page.waitForTimeout(700);
  const dialogOpen = await page.$eval("#jobDialog", (d) => d.open);
  rec("J4 双击列表项能打开编辑对话框", dialogOpen, `dialog.open=${dialogOpen}`);
  before = posts.jobs;
  await page.click("#saveJob", { clickCount: 2, delay: 30 });
  await page.waitForTimeout(1800);
  const saveGuarded = posts.jobs - before;
  rec("J5 连点「保存」只发 1 个 POST", saveGuarded === 1, `POST ${saveGuarded} 次`);

  // ---- B') 负向对照 ----
  await bypassGuard();
  await page.dblclick("#jobList .job-item");
  await page.waitForTimeout(700);
  before = posts.jobs;
  await page.click("#saveJob", { clickCount: 2, delay: 30 });
  await page.waitForTimeout(1800);
  const saveUnguarded = posts.jobs - before;
  rec("J6 负向对照：摘掉守卫后连点「保存」确实发 2 个 POST", saveUnguarded === 2,
    `POST ${saveUnguarded} 次（证明 J5 不是空转）`);
  await restoreGuard();
  await page.waitForTimeout(800);

  // ---- C) Q-002 取消后状态重置 ----
  await page.dblclick("#jobList .job-item");
  await page.waitForTimeout(700);
  const readDraft = () => page.evaluate(() => ({
    editingJobId: typeof editingJobId === "undefined" ? "N/A" : editingJobId,
    steps: typeof draftSteps === "undefined" ? -1 : draftSteps.length,
    stepIndex: typeof selectedStepIndex === "undefined" ? -2 : selectedStepIndex,
  }));
  const dirty = await readDraft();
  rec("J7 打开对话框后确实装载了草稿（对照用）",
    dirty.editingJobId !== "" && dirty.steps >= 1, `id=${dirty.editingJobId} 步骤=${dirty.steps}`);
  await page.click("#cancelJob");
  await page.waitForTimeout(600);
  const clean = await readDraft();
  rec("J8「取消」后草稿状态已清零（不再残留污染下一次编辑）",
    clean.editingJobId === "" && clean.steps === 0 && clean.stepIndex === -1,
    `id="${clean.editingJobId}" 步骤=${clean.steps} 选中=${clean.stepIndex}`);

  // ---------------------------------------------------------------- 定时任务页
  await page.goto(BASE + "/schedule.html", { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  const schedRows = await page.$$("#scheduleTableBody tr[data-id]");
  rec("S0 定时任务页加载、沙箱里已有预置任务", schedRows.length >= 1, `列表 ${schedRows.length} 条`);

  // 选中第一行
  await page.evaluate(() => {
    const el = document.querySelector("#scheduleTableBody tr[data-id]");
    if (el) el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await page.waitForTimeout(400);

  before = posts.run;
  await page.click("#runScheduleNow", { clickCount: 2, delay: 30 });
  await page.waitForTimeout(2000);
  const runGuarded = posts.run - before;
  rec("S1 连点「立即执行」只触发 1 次作业运行", runGuarded === 1, `POST ${runGuarded} 次`);

  await bypassGuard();
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    const el = document.querySelector("#scheduleTableBody tr[data-id]");
    if (el) el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await page.waitForTimeout(400);
  before = posts.run;
  await page.click("#runScheduleNow", { clickCount: 2, delay: 30 });
  await page.waitForTimeout(2000);
  const runUnguarded = posts.run - before;
  rec("S2 负向对照：摘掉守卫后连点「立即执行」确实触发 2 次", runUnguarded === 2,
    `POST ${runUnguarded} 次（证明 S1 不是空转）`);
  await restoreGuard();
  await page.waitForTimeout(900);

  // ---- S3) 启停按钮的 disabled 由 updateToolbarState 决定，还原后不能把本该灰的按钮点亮 ----
  await page.evaluate(() => {
    const el = document.querySelector("#scheduleTableBody tr[data-id]");
    if (el) el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await page.waitForTimeout(400);
  const beforeState = await page.evaluate(() => ({
    start: document.getElementById("startSchedule").disabled,
    pause: document.getElementById("pauseSchedule").disabled,
  }));
  await page.click("#pauseSchedule");
  await page.waitForTimeout(1500);
  const afterState = await page.evaluate(() => ({
    start: document.getElementById("startSchedule").disabled,
    pause: document.getElementById("pauseSchedule").disabled,
  }));
  rec("S3 停用后启停按钮的灰态由选中态重算（未被守卫还原逻辑点亮）",
    afterState.start === false && afterState.pause === true,
    `停用前 start=${beforeState.start}/pause=${beforeState.pause} → 停用后 start=${afterState.start}/pause=${afterState.pause}`);

  rec("Z1 全程无 JS 报错", pageErrors.length === 0, pageErrors.join(" | ") || "pageerror=0");

  const failed = results.filter((r) => !r.ok);
  console.log(`\n== 合计 ${results.length} 项，PASS ${results.length - failed.length}，FAIL ${failed.length} ==`);
  if (failed.length) failed.forEach((f) => console.log("  FAIL: " + f.name));
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
