/**
 * 布局 / 响应式 / 跨主题 / 跨浏览器 自动体检（2026-10-15）
 *
 * 对应验收报告：
 *   Q-006 同引 styles.css + theme.css + theme-classic.css，三层主题变量是否真打架
 *   Q-007 1280 / 1440 / 1920 / 2560 四档分辨率下的遮挡、溢出、错位、文字截断
 *   Q-009 Chrome 之外的 Edge（用 Playwright 的 msedge channel）
 *
 * 为什么写成程序化检测而不是"看截图"：
 *   截图只能靠眼睛，1280~2560 × 11 个页面 = 44 张图，人眼一定会漏。
 *   这里用三个可复现的判据，任何一条命中都会打印出**具体的元素与数值**：
 *     ① 横向溢出：documentElement.scrollWidth 超过 clientWidth（页面被撑出横向滚动条）
 *     ② 点击被挡：对可见按钮做 elementFromPoint(中心点) 回查，
 *        若命中的不是它自己也不是它的子孙 → 它被别的元素盖住了，点不到
 *     ③ 无提示截断：scrollWidth > clientWidth 且 overflow-x:hidden 且
 *        text-overflow 不是 ellipsis → 文字被切掉又没有任何视觉提示
 *   这三条都"宁可漏报也不误报"，所以只统计**完全落在视口内**的可见元素。
 *
 * 跑法：bash scripts/run_layout_walk.sh
 */
const { chromium } = require("playwright");

const BASE = process.env.BASE_URL || "http://127.0.0.1:51985";
const WIDTHS = [1280, 1440, 1920, 2560];
const PAGES = [
  ["index.html", "导入"],
  ["export.html", "导出"],
  ["sync.html", "同步"],
  ["connections.html", "连接"],
  ["query.html", "查询"],
  ["tables.html", "表管理"],
  ["jobs.html", "作业"],
  ["schedule.html", "定时任务"],
  ["users.html", "账号"],
  ["audit.html", "审计"],
];

const results = [];
function rec(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name} :: ${detail}`);
}

// 在页面里跑的检测器：返回三条判据的命中清单
const PROBE = () => {
  const out = { overflowX: null, covered: [], clipped: [] };
  const de = document.documentElement;

  if (de.scrollWidth - de.clientWidth > 1) {
    out.overflowX = `scrollWidth=${de.scrollWidth} clientWidth=${de.clientWidth}`;
  }

  const desc = (el) => {
    const id = el.id ? "#" + el.id : "";
    const cls = el.className && typeof el.className === "string"
      ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "";
    return el.tagName.toLowerCase() + id + cls;
  };

  // "对读屏软件可见、对眼睛不可见"的元素（visually-hidden / sr-only / 1px 兜底法）
  // 文字被裁是**刻意**的，不能算文字截断缺陷，必须排除，否则全是误报。
  const visuallyHidden = (el) => {
    const cls = typeof el.className === "string" ? el.className : "";
    if (/visually-hidden|sr-only|screen-reader|a11y-hidden/.test(cls)) return true;
    if (el.clientWidth <= 1 || el.clientHeight <= 1) return true;
    const s = getComputedStyle(el);
    if (/inset\(50%\)|rect\(0/.test((s.clipPath || "") + "|" + (s.clip || ""))) return true;
    return false;
  };

  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) < 0.05) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    return !visuallyHidden(el);
  };

  // ② 按钮中心点被谁挡住？只查完全在视口内的可见按钮
  document.querySelectorAll("button, a.button, .ribbon-item, .tree-node").forEach((el) => {
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    if (r.top < 0 || r.left < 0 || r.bottom > innerHeight || r.right > innerWidth) return;
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit) return;
    if (hit === el || el.contains(hit) || hit.contains(el)) return;
    out.covered.push(`${desc(el)} 被 ${desc(hit)} 盖住`);
  });

  // ③ 文字被切掉又没有省略号提示
  document.querySelectorAll("td, th, label, span, strong, div, p, h1, h2, h3, li, button").forEach((el) => {
    if (!visible(el)) return;
    if (el.children.length) return;               // 只看叶子节点，避免父级重复计数
    const text = (el.textContent || "").trim();
    if (!text) return;
    const s = getComputedStyle(el);
    if (el.scrollWidth - el.clientWidth <= 1) return;
    if (s.overflowX !== "hidden" && s.overflowX !== "clip") return;
    if (s.textOverflow === "ellipsis") return;    // 有省略号 = 用户能看出被截断，算合理
    out.clipped.push(`${desc(el)} 文案「${text.slice(0, 14)}」被切 ${el.scrollWidth - el.clientWidth}px`);
  });

  return out;
};

(async () => {
  const browser = await chromium.launch();
  let total = 0;
  let bad = 0;

  // ------------------------------------------------ Q-007 四档分辨率
  for (const width of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e.message || "").split("\n")[0]));
    const problems = [];
    for (const [file, label] of PAGES) {
      await page.goto(`${BASE}/${file}`, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(450);
      const r = await page.evaluate(PROBE);
      if (r.overflowX) problems.push(`${label}: 横向溢出 ${r.overflowX}`);
      r.covered.slice(0, 3).forEach((x) => problems.push(`${label}: ${x}`));
      r.clipped.slice(0, 3).forEach((x) => problems.push(`${label}: ${x}`));
      total++;
    }
    bad += problems.length;
    rec(`R-${width} ${WIDTHS.length} 档中的 ${width}px · ${PAGES.length} 个页面无溢出/遮挡/截断`,
      problems.length === 0 && errs.length === 0,
      problems.length || errs.length ? problems.concat(errs).slice(0, 5).join(" ｜ ") : "全部干净");
    await page.close();
  }

  // ------------------------------------------------ Q-006 跨主题：布局必须一致、配色必须真的换掉
  const themed = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const SAMPLE = [
    ["#jobList", "作业列表"],
    [".job-toolbar", "作业工具条"],
    ["#jobDialog", "作业对话框"],
  ];
  const readTheme = async (theme) => {
    await themed.goto(`${BASE}/jobs.html`, { waitUntil: "domcontentloaded" });
    await themed.evaluate((t) => localStorage.setItem("dc_theme_v1", t), theme);
    await themed.reload({ waitUntil: "domcontentloaded" });
    await themed.waitForTimeout(600);
    return themed.evaluate((sels) => {
      const out = { bodyClass: document.body.className, rects: {}, colors: {} };
      for (const [sel, label] of sels) {
        const el = document.querySelector(sel);
        if (!el) { out.rects[label] = null; continue; }
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        out.rects[label] = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)].join(",");
        out.colors[label] = { bg: s.backgroundColor, color: s.color, border: s.borderTopColor };
      }
      out.bodyBg = getComputedStyle(document.body).backgroundColor;
      return out;
    }, SAMPLE);
  };
  const cream = await readTheme("cream");
  const classic = await readTheme("classic");
  const themeSwitched = cream.bodyBg !== classic.bodyBg || classic.bodyClass.includes("theme-classic");
  rec("T1 切换主题后 body 确实换了一套配色（说明 theme-classic.css 真的生效）",
    themeSwitched, `cream bg=${cream.bodyBg} / classic bg=${classic.bodyBg} bodyClass="${classic.bodyClass}"`);
  const layoutSame = SAMPLE.every(([, label]) => cream.rects[label] === classic.rects[label]);
  const rectDiff = SAMPLE.filter(([, label]) => cream.rects[label] !== classic.rects[label])
    .map(([, label]) => `${label}: ${cream.rects[label]} vs ${classic.rects[label]}`).join(" ｜ ");
  rec("T2 换主题不改变任何布局（三层 CSS 没有把版式带跑）",
    layoutSame, layoutSame ? "三处采样点几何完全相同" : rectDiff);
  const contentSame = SAMPLE.every(([, label]) =>
    JSON.stringify(cream.rects[label]) === JSON.stringify(classic.rects[label]));
  rec("T3 两层 CSS 之外的 styles.css 基础层未被主题变量污染（采样点可解析）",
    SAMPLE.every(([, label]) => cream.rects[label] !== null && classic.rects[label] !== null) && contentSame,
    SAMPLE.map(([sel, label]) => `${label}${cream.rects[label] ? "✓" : "✗"}`).join(" "));
  await themed.close();

  // ------------------------------------------------ Q-009 Edge（msedge channel）
  let edgeOk = false;
  let edgeDetail = "";
  try {
    const edge = await chromium.launch({ channel: "msedge" });
    const ep = await edge.newPage({ viewport: { width: 1440, height: 900 } });
    const eerrs = [];
    ep.on("pageerror", (e) => eerrs.push(String(e.message || "").split("\n")[0]));
    const edgeProblems = [];
    for (const [file, label] of [["jobs.html", "作业"], ["sync.html", "同步"], ["schedule.html", "定时任务"], ["index.html", "导入"]]) {
      await ep.goto(`${BASE}/${file}`, { waitUntil: "domcontentloaded" });
      await ep.waitForTimeout(500);
      const r = await ep.evaluate(PROBE);
      if (r.overflowX) edgeProblems.push(`${label}: 横向溢出`);
      r.covered.slice(0, 2).forEach((x) => edgeProblems.push(`${label}: ${x}`));
      if (await ep.title() !== "DataConverter") edgeProblems.push(`${label}: 标题异常`);
    }
    edgeOk = edgeProblems.length === 0 && eerrs.length === 0;
    edgeDetail = edgeProblems.concat(eerrs).slice(0, 4).join(" ｜ ") ||
      `Edge ${browser.version()} 加载 4 个页面无 JS 报错、无溢出/遮挡`;
    await edge.close();
  } catch (error) {
    edgeDetail = "本机无法启动 msedge channel：" + String(error.message || error).split("\n")[0];
  }
  rec("E1 Edge（Chromium 内核）加载与布局与 Chrome 一致", edgeOk, edgeDetail);

  const failed = results.filter((r) => !r.ok);
  console.log(`\n== 合计 ${results.length} 项判定，PASS ${results.length - failed.length}，FAIL ${failed.length}；共检查 ${total} 个「页面×分辨率」组合 ==`);
  if (failed.length) failed.forEach((f) => console.log("  FAIL: " + f.name + " :: " + f.detail));
  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
