/* ============================================================================
   同步页面（public/sync.html）真实走查：起服务 → 打开页面 → 逐项验证交互。

   用法：
     CODEBUDDY_SAFE_DELETE_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
       NODE_PATH="C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules" \
       "C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe" \
       acceptance/ui_walk_sync.js

   ⚠️ **只写隔离沙箱**：不点「立即同步」（链路端到端由 verify_sync_api.py 覆盖），
      但 L 段的"新建 / 删除任务"会真的写库 —— 沙箱自带 DATA_DIR，碰不到实时实例。

   两层结构（业主 2026-10-10 补充）：
     第一层 /sync.html        任务列表 → 新建 / 修改 / 删除 / 双击打开
     第二层 /sync-canvas.html 画布 → 5 节点配置 / 预览 / 保存 / 立即同步
   L 段验证第一层，1–18 段验证第二层。

   关注四件事：
     1. 有没有 pageerror（哪怕一条都不许有）
     2. 两层之间的跳转与回退是不是通的（列表 ↔ 画布）
     3. 五个节点卡面是不是真的从数据推导出来的（不是写死的占位）
     4. 点节点 → 面板 → 改配置 → 卡面同步变化（体现单一真值源）
   ============================================================================ */
const { chromium } = require("playwright");

const BASE = process.env.SYNC_BASE || "http://127.0.0.1:51983";
const results = [];
function rec(case_, ok, detail) {
  results.push([ok ? "PASS" : "FAIL", case_, detail]);
  console.log(`[${ok ? "PASS" : "FAIL"}] ${case_} :: ${detail}`);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1680, height: 950 } });
  const errors = [];
  // 原生弹窗（alert / confirm / beforeunload）单独记一笔：业主报过
  // 「点新建同步任务却弹『离开此网站？』」，那是跳页触发了 shell.js 的离开提醒。
  const nativeDialogs = [];
  page.on("pageerror", (e) => {
    errors.push(String(e.message || e));
    // 立刻打出来：否则要等走查跑完才知道中途出过什么错
    console.log("  !! pageerror:", String(e.message || e).split("\n")[0]);
  });
  page.on("console", (m) => {
    if (m.type() === "error") errors.push("console: " + m.text());
  });
  page.on("dialog", (d) => {
    nativeDialogs.push(d.type());
    d.accept();
  });

  /* ================= 第一层：任务列表（/sync.html） ================= */
  // 沙箱里先放两条任务，否则列表是空的，双击 / 删除都测不到。
  async function apiTask(name, mode) {
    const resp = await page.request.post(BASE + "/api/sync/tasks", {
      data: {
        name, sourceConnectionId: "src", targetConnectionId: "dst",
        sourceTable: "大表", targetTable: "walk_" + name, syncMode: mode || "full",
      },
    });
    return (await resp.json()).task;
  }
  const seededA = await apiTask("走查任务A");
  const doomed = await apiTask("走查待删除任务");

  await page.goto(BASE + "/sync.html", { waitUntil: "networkidle" });
  await page.waitForTimeout(700);

  rec("1 页面加载无 JS 报错", errors.length === 0, errors.join(" | ") || "pageerror=0");
  rec("L1 落在第一层「任务列表」而不是直接进画布",
    await page.$eval("body", (b) => b.dataset.page) === "sync" && !!(await page.$("#taskList")),
    "body[data-page=sync] + #taskList");
  const tools = await page.$$eval("#listView .sync-toolbar button", (bs) => bs.map((b) => b.textContent.trim()));
  rec("L2 模块层只留三个按钮（打开同步 / 新建同步任务 / 删除）",
    JSON.stringify(tools) === JSON.stringify(["打开同步", "新建同步任务", "删除"]),
    tools.join(" / "));
  const rows = await page.$$eval("#taskList .c-name", (ns) => ns.map((n) => n.textContent.trim()));
  rec("L3 列表列出了已存任务（含刚种下的两条）",
    rows.includes("走查任务A") && rows.includes("走查待删除任务"),
    rows.join("、") + `（共 ${rows.length} 条）`);
  const modeCells = await page.$$eval("#taskList .c-mode", (ns) => ns.map((n) => n.textContent.trim()));
  rec("L4 行里显示同步模式中文名（不是英文枚举）",
    modeCells.length > 0 && modeCells.every((m) => m === "全量覆盖"), modeCells.join("、"));
  const rowRuns = await page.$$eval("#taskList .c-run .st, #taskList .c-run .muted",
    (ns) => ns.map((n) => n.textContent.trim()));
  rec("L5 行里显示「上次运行」状态（没有记录时明确写从未运行）",
    rowRuns.some((t) => t === "从未运行"), rowRuns.slice(0, 4).join(" | "));

  const srcCells = await page.$$eval("#taskList .c-src", (ns) => ns.map((n) => n.textContent.trim()));
  const dstCells = await page.$$eval("#taskList .c-dst", (ns) => ns.map((n) => n.textContent.trim()));
  // 连接对象里库名字段叫 database，写成 dbName 会静默显示成空 —— 断言前缀守住它。
  rec("L6 源/目标列带库名前缀（库名字段取对了）",
    srcCells.length > 0 && srcCells.every((t) => t.startsWith("dc_sync_src.")) &&
      dstCells.every((t) => t.startsWith("dc_sync_dst.")),
    `${srcCells[0]} → ${dstCells[0]}`);

  // 卡片必须撑满内容区：表格用了 table-layout:fixed，一旦宽度没写死就会被收窄。
  const widths = await page.evaluate(() => {
    const r = (s) => Math.round(document.querySelector(s).getBoundingClientRect().width);
    return { ws: r(".app-workspace"), bar: r(".sync-toolbar"), table: r(".sync-table") };
  });
  rec("L7 列表卡片撑满内容区（工具条与表格都不被收窄）",
    widths.bar >= widths.ws - 40 && widths.table >= widths.ws - 40,
    `内容区 ${widths.ws} / 工具条 ${widths.bar} / 表格 ${widths.table}`);

  // 单击只选中，双击才进画布
  await page.screenshot({ path: "acceptance/evidence/sync-list.png", fullPage: false });

  await page.click(`#taskList tr[data-id="${seededA.id}"]`);
  await page.waitForTimeout(400);
  rec("L8 单击只是选中（停留在列表层）",
    page.url().includes("/sync.html") &&
    (await page.$$eval("#taskList tr.on", (ns) => ns.length)) === 1,
    "URL 未跳转，选中 1 行");
  const runsBox = await page.$eval("#runList", (n) => n.textContent.trim());
  rec("L9 选中后加载该任务的运行历史区", runsBox.length > 0, runsBox.slice(0, 40) + "…");

  const urlBefore = page.url();
  await page.dblclick(`#taskList tr[data-id="${seededA.id}"]`);
  await page.waitForTimeout(900);
  const viewState = await page.evaluate(() => ({
    list: document.getElementById("listView").classList.contains("on"),
    canvas: document.getElementById("canvasView").classList.contains("on"),
  }));
  rec("L10 双击一行切到画布视图（同页切换，不是跳页）",
    viewState.canvas && !viewState.list && page.url() === urlBefore,
    `canvasView.on=${viewState.canvas} / listView.on=${viewState.list}，URL 未变`);
  const cbName = await page.$eval("#cbName", (n) => n.textContent.trim());
  const cbFlag = await page.$eval("#cbFlag", (n) => n.textContent.trim());
  rec("L11 顶部条显示这一行的任务名与保存状态",
    cbName === "走查任务A" && cbFlag === "已保存", `「${cbName}」·「${cbFlag}」`);
  rec("L12 切换视图没有弹原生窗（业主报的「离开此网站？」）",
    nativeDialogs.length === 0, nativeDialogs.join(",") || "原生弹窗 0 个");

  // 回列表：点顶部工具条那个按钮
  await page.click("#backToList");
  await page.waitForTimeout(600);
  const backState = await page.evaluate(() => ({
    list: document.getElementById("listView").classList.contains("on"),
    canvas: document.getElementById("canvasView").classList.contains("on"),
  }));
  rec("L13 顶部「← 同步任务」能回列表（不再依赖跳页）",
    backState.list && !backState.canvas && page.url() === urlBefore,
    "listView.on=true，URL 仍未变");

  // 「打开同步」= 打开选中的那一行（原「修改」按钮改名而来）
  await page.click(`#taskList tr[data-id="${seededA.id}"]`);
  await page.waitForTimeout(300);
  await page.click("#openTask");
  await page.waitForTimeout(900);
  const openedName = await page.$eval("#cbName", (n) => n.textContent.trim());
  const openedOnCanvas = await page.$eval("#canvasView", (n) => n.classList.contains("on"));
  rec("L14「打开同步」按钮打开的是选中的那个任务",
    openedOnCanvas && openedName === "走查任务A", `顶部条「${openedName}」`);
  await page.click("#backToList");
  await page.waitForTimeout(700);

  // 新建：进空白画布，且不会顺手打开别人的任务
  await page.click("#newTask");
  await page.waitForTimeout(800);
  const newFlag = await page.$eval("#cbFlag", (n) => n.textContent.trim());
  const newName = await page.$eval("#cbName", (n) => n.textContent.trim());
  const nodeCount0 = await page.$$eval("#world .node", (ns) => ns.map((n) => n.id));
  rec("L15「新建同步任务」进的是空白画布，且只放了一个起点节点",
    newFlag === "新建 · 尚未保存" && newName === "未命名任务" &&
      nodeCount0.length === 1 && nodeCount0[0] === "n-src",
    `「${newName}」·「${newFlag}」· 节点 ${nodeCount0.join(",") || "无"}`);

  /* --------- 问题 4：任务名要落在节点 1 上 --------- */
  await page.click("#n-src");
  await page.waitForTimeout(400);
  const srcPanel = await page.$eval("#drawerBody", (b) => b.textContent);
  const nameField = await page.$("#drawerBody [data-field='name']");
  rec("L16 节点 1 面板里有「任务」小节（任务名 + 启用开关）",
    srcPanel.includes("任务名称") && srcPanel.includes("启用这个同步任务") && !!nameField,
    "面板含任务名输入与启用开关");
  await page.screenshot({ path: "acceptance/evidence/sync-drawer.png", fullPage: false });

  await page.fill("#drawerBody [data-field='name']", "走查分层的任务");
  await page.click("#dwOk");
  await page.waitForTimeout(500);
  const srcCard = await page.$eval("#n-src .n-lines", (n) => n.textContent);
  rec("L17 源节点卡面第 1 行就显示任务名",
    srcCard.trim().startsWith("任务"), srcCard.replace(/\s+/g, " ").slice(0, 46) + "…");
  const topAfterName = await page.$eval("#cbName", (n) => n.textContent.trim());
  rec("L18 改名后画布顶部同步更新（单一真值源）",
    topAfterName === "走查分层的任务", `顶部条「${topAfterName}」`);

  // 启用开关真的可切
  await page.click("#n-src");
  await page.waitForTimeout(300);
  await page.click("#drawerBody [data-toggle='enabled']");
  await page.waitForTimeout(300);
  const enabledOff = await page.$eval("#drawerBody [data-toggle='enabled']", (n) => n.classList.contains("on"));
  await page.click("#dwOk");
  await page.waitForTimeout(400);
  const srcCardOff = await page.$eval("#n-src .n-lines", (n) => n.textContent);
  rec("L19 启用开关可切换，且卡面标注「已停用」",
    enabledOff === false && srcCardOff.includes("已停用"), srcCardOff.replace(/\s+/g, " ").slice(0, 40) + "…");

  /* ================= 问题 3：节点按需添加 + 连线可拖拽 ================= */
  await page.click("#n-src .op-add");
  await page.waitForTimeout(400);
  const menuOpen = await page.$eval("#addMenu", (m) => !m.hidden);
  const menuItems = await page.$$eval("#addMenu .am-item", (bs) => bs.map((b) => b.dataset.kind));
  rec("L20 点节点上的「＋」才列出候选（默认不铺满全部节点）",
    menuOpen && ["map", "mode", "dst", "skip"].every((k) => menuItems.includes(k)) &&
      !menuItems.includes("src"),
    "候选：" + menuItems.join("、"));

  async function addNodeFrom(fromKind, wantKind) {
    await page.click(`#n-${fromKind} .op-add`);
    await page.waitForTimeout(350);
    await page.click(`#addMenu .am-item[data-kind="${wantKind}"]`);
    await page.waitForTimeout(700);
  }

  await page.click('#addMenu .am-item[data-kind="map"]');
  await page.waitForTimeout(700);
  let flow = await page.evaluate(() => window.__flow());
  rec("L21 加「字段映射」后节点出现，并且自动连了一条线",
    flow.nodes.length === 2 && flow.nodes.includes("map") && flow.edges.includes("src→map"),
    `${flow.nodes.join("+")}；连线 ${flow.edges.join("，")}`);

  await addNodeFrom("map", "mode");
  await addNodeFrom("mode", "dst");
  flow = await page.evaluate(() => window.__flow());
  rec("L22 逐步补齐到「源 → 字段映射 → 同步模式 → 目标」，连线同步串起来",
    flow.nodes.length === 4 &&
      ["src→map", "map→mode", "mode→dst"].every((e) => flow.edges.includes(e)),
    `${flow.nodes.join("→")}；连线 ${flow.edges.join("，")}`);

  // 拖连线的目标端：把 map→mode 改接到「目标」上
  const hookPos = await page.$eval('#edges .edge-hook[data-edge="1"]', (c) => {
    const r = c.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  const dstPos = await page.$eval("#n-dst", (n) => {
    const r = n.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  await page.mouse.move(hookPos.x, hookPos.y);
  await page.mouse.down();
  await page.mouse.move(dstPos.x, dstPos.y, { steps: 14 });
  await page.mouse.up();
  await page.waitForTimeout(700);
  flow = await page.evaluate(() => window.__flow());
  rec("L23 连线可以拖拽改接（map→mode 改成了 map→dst）",
    flow.edges.includes("map→dst") && !flow.edges.includes("map→mode"),
    "连线现在是 " + flow.edges.join("，"));

  // 节点位置也能拖（拖节点的标题栏，避开 ＋/− 按钮）
  const srcLeftBefore = await page.$eval("#n-src", (n) => n.style.left);
  const headBox = await page.$eval("#n-src .n-head", (n) => {
    const r = n.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  await page.mouse.move(headBox.x, headBox.y);
  await page.mouse.down();
  await page.mouse.move(headBox.x + 70, headBox.y + 110, { steps: 14 });
  await page.mouse.up();
  await page.waitForTimeout(700);
  const srcAfterDrag = await page.$eval("#n-src", (n) => ({ left: n.style.left, top: n.style.top }));
  const drawerAfterDrag = await page.$eval("#drawer", (d) => d.classList.contains("open"));
  rec("L24 节点可以拖着挪位置，且拖完不会误弹出配置面板",
    srcAfterDrag.left !== srcLeftBefore && !drawerAfterDrag,
    `${srcLeftBefore} → ${srcAfterDrag.left}/${srcAfterDrag.top}，面板 open=${drawerAfterDrag}`);

  /* ================= 第二层：画布（同页，已完成切换） ================= */
  await page.waitForTimeout(400);

  const cards = await page.$$eval(".node", (ns) =>
    ns.map((n) => ({
      id: n.id,
      type: n.dataset.type,
      title: n.querySelector(".n-title").textContent,
      state: n.querySelector(".n-state").textContent,
      lines: [...n.querySelectorAll(".n-line")].map((l) => l.textContent),
    }))
  );
  rec("2 画布上是刚才按需加出来的那 4 个节点",
    cards.length === 4, cards.map((c) => c.type).join(" → "));
  // 源节点 4 行（第 1 行是任务名），其余 3 行。
  rec("3 卡面要点齐备：源 4 行（含任务名），其余各 3 行",
    cards.every((c) => c.lines.length === (c.type === "src" ? 4 : 3)),
    cards.map((c) => `${c.title}:${c.lines.length}`).join(" "));
  rec("4 卡面内容由数据推导（没有写死的示例表名）",
    !cards.some((c) => c.lines.join("").includes("会员小票表")),
    "未出现原型里的硬编码表名");

  // ---- 打开「同步模式」节点，切到增量，看面板的反应 ----
  await page.click("#n-mode");
  await page.waitForTimeout(400);
  const drawerOpen = await page.$eval("#drawer", (d) => d.classList.contains("open"));
  rec("5 点节点右侧面板滑出", drawerOpen, "drawer.open=true");

  const modeCards = await page.$$eval('.opts[data-group="syncMode"] .opt', (os) =>
    os.map((o) => ({ val: o.dataset.val, on: o.classList.contains("on"), disabled: o.classList.contains("disabled") }))
  );
  rec("6 模式候选来自服务端（4 档且全部可用）",
    modeCards.length === 4 && modeCards.every((m) => !m.disabled),
    modeCards.map((m) => m.val).join("、"));

  await page.click('.opts[data-group="syncMode"] .opt[data-val="incremental"]');
  await page.waitForTimeout(300);
  const incPanel = await page.$eval("#drawerBody", (b) => b.textContent);
  rec("7 切到增量后自动出现「增量设置」分区", incPanel.includes("增量设置"), "面板含水位列区");

  await page.click('.opts[data-group="syncMode"] .opt[data-val="full"]');
  await page.waitForTimeout(300);
  const fullPanel = await page.$eval("#drawerBody", (b) => b.textContent);
  rec("8 切回全量后「增量设置」消失（非法组合即选即禁用）",
    !fullPanel.includes("增量设置"), "面板已不含水位列区");

  // 面板中改配置，卡面必须同步（这是"单一真值源"的可观测证据）
  const beforeDst = await page.$eval("#n-dst .n-lines", (n) => n.textContent);
  await page.click("#n-mode");
  await page.waitForTimeout(250);
  await page.click('.opts[data-group="syncMode"] .opt[data-val="append"]');
  await page.waitForTimeout(300);
  const afterDst = await page.$eval("#n-dst .n-lines", (n) => n.textContent);
  rec("9 改同步模式后「目标」节点的写入方式同步变化",
    beforeDst !== afterDst && afterDst.includes("直接插入"),
    `目标卡面：${afterDst.slice(0, 40)}…`);

  // ---- 一致性自检必须为空 ----
  const bad = await page.evaluate(() => (window.__taskConsistency ? window.__taskConsistency() : ["(未暴露)"]));
  rec("10 跨节点一致性自检为空", Array.isArray(bad) && bad.length === 0, JSON.stringify(bad));

  // ---- 底部三个按钮存在且可点 ----
  const btns = await page.$$eval(".flow-actions button", (bs) => bs.map((b) => b.textContent.trim()));
  rec("11 右下只有三个按钮且顺序正确",
    JSON.stringify(btns) === JSON.stringify(["任务预览", "保存任务", "立即同步"]), btns.join(" / "));

  const hud = await page.$$eval(".flow-hud button", (bs) => bs.length);
  rec("12 左下缩放控件在", hud === 3, `${hud} 个按钮`);

  await page.click("#zoomIn");
  await page.waitForTimeout(200);
  const zoom = await page.$eval("#zoomVal", (z) => z.textContent);
  rec("13 缩放可用且读数更新", zoom !== "100%", `当前 ${zoom}`);

  // ---- 接真实库：选连接 -> 选表 -> 任务预览（只读，不写任何东西）----
  await page.click("#n-src");
  await page.waitForTimeout(400);
  const connCount = await page.$$eval('[data-field="sourceConnectionId"] option', (o) => o.length);
  rec("14 连接下拉里有真实连接", connCount > 1, (connCount - 1) + " 个连接");

  await page.selectOption('[data-field="sourceConnectionId"]', { index: 1 });
  await page.waitForTimeout(1500);
  const tableOpts = await page.$$eval('[data-field="sourceTable"] option', (o) => o.length);
  rec("15 源表候选来自真实连接（不是硬编码）", tableOpts > 1, (tableOpts - 1) + " 张表");

  await page.selectOption('[data-field="sourceTable"]', { index: 1 });
  await page.waitForTimeout(2000);
  const srcCardProbe = await page.$eval("#n-src .n-lines", (n) => n.textContent);
  rec("16 探测结果回填到源卡面（行数 / 列数）",
    /\d/.test(srcCardProbe), srcCardProbe.replace(/\s+/g, " ").slice(0, 64) + "...");

  // 面板是盖在画布右侧的，先关掉再点下一个节点（真实用户就是这个动作）
  await page.click("#dwClose");
  await page.waitForTimeout(400);
  await page.click("#n-dst");
  await page.waitForTimeout(400);
  await page.selectOption('[data-field="targetConnectionId"]', { index: 2 });
  await page.waitForTimeout(1200);
  await page.fill('[data-field="targetTable"]', "ui_walk_target");
  await page.click("#dwOk");
  await page.waitForTimeout(400);

  await page.click("#btnPreview");
  await page.waitForTimeout(4000);
  const dlg = await page.$eval("#dlgBody", (b) => b.textContent);
  rec("17 任务预览真的跑通（出结论不报错）",
    dlg.includes("这一跑会发生什么") && dlg.includes("本轮将读取"),
    dlg.replace(/\s+/g, " ").slice(0, 100) + "...");
  await page.click("#dlgClose");
  await page.waitForTimeout(300);
  // 对话框必须真的关得掉 —— 画布把 <dialog> 包在 .stage 里，早先版本点「×」
  // 会被画布的平移逻辑 `setPointerCapture` 劫走指针事件，按钮根本点不动。
  const stillOpen = await page.$$eval("dialog[open]", (ds) => ds.map((d) => d.id));
  rec("L25 对话框里的按钮点得动（点「×」后没有残留打开的弹窗）",
    stillOpen.length === 0, stillOpen.join(",") || "无残留 open");

  // 保存 → 回列表能看到。这是两层之间最容易断的一环：存了却在列表里找不到，等于没存。
  // 任务名已经在 L15 填过，这里不会弹命名框；万一弹了就补填一次。
  await page.click("#btnSave");
  await page.waitForTimeout(600);
  if (await page.$eval("#nameDialog", (d) => d.open)) {
    await page.fill("#nameInput", "走查分层的任务");
    await page.click("#nameOk");
  }
  await page.waitForTimeout(1800);
  const saveDlg = await page.$eval("#dlgBody", (b) => b.textContent);
  rec("L26 保存后给出确认，并把回列表的路指出来",
    saveDlg.includes("走查分层的任务") && saveDlg.includes("同步任务"),
    saveDlg.replace(/\s+/g, " ").slice(0, 70) + "…");
  await page.click("#dlgClose");
  await page.waitForTimeout(300);
  const cbAfter = await page.$eval("#cbFlag", (n) => n.textContent.trim());
  rec("L27 保存后顶部条转为「已保存」（同页，URL 本就不用变）",
    cbAfter === "已保存", `标记「${cbAfter}」`);

  await page.screenshot({ path: "acceptance/evidence/sync-page.png", fullPage: false });

  /* ================= 回第一层：新任务在列表里 / 删除任务 ================= */
  await page.click("#backToList");
  await page.waitForTimeout(900);
  const namesNow = await page.$$eval("#taskList .c-name", (ns) => ns.map((n) => n.textContent.trim()));
  rec("L28 刚保存的任务出现在列表里（两层闭环）",
    namesNow.some((n) => n.includes("走查分层的任务")), namesNow.join("、"));
  const beforeDel = await page.$$eval("#taskList tr[data-id]", (ns) => ns.length);
  await page.click(`#taskList tr[data-id="${doomed.id}"]`);
  await page.waitForTimeout(300);
  await page.click("#deleteTask");
  await page.waitForTimeout(400);
  const delBody = await page.$eval("#listDlgBody", (n) => n.textContent);
  rec("L29 删除前弹确认，并说清连带清掉什么",
    delBody.includes("走查待删除任务") && delBody.includes("不可撤销") && delBody.includes("历史"),
    delBody.replace(/\s+/g, " ").slice(0, 60) + "…");

  await page.click("#listDlgOk", { force: true });
  await page.waitForTimeout(1200);
  const afterDel = await page.$$eval("#taskList tr[data-id]", (ns) => ns.length);
  rec("L30 确认后这一行真的消失了", afterDel === beforeDel - 1, `${beforeDel} → ${afterDel} 行`);

  const apiLeft = (await (await page.request.get(BASE + "/api/sync/tasks")).json()).tasks
    .map((t) => t.id);
  rec("L31 服务端也删干净了（不是只在界面上藏起来）",
    !apiLeft.includes(doomed.id) && apiLeft.includes(seededA.id),
    `剩余 ${apiLeft.length} 条`);

  rec("18 无 JS 报错（走查结束再确认一次）", errors.length === 0, errors.join(" | ") || "pageerror=0");
  rec("L32 全程没有原生弹窗（离开提醒 / confirm 都不该出现）",
    nativeDialogs.length === 0, nativeDialogs.join(",") || "原生弹窗 0 个");

  await browser.close();
  const passed = results.filter((r) => r[0] === "PASS").length;
  console.log("\n" + "=".repeat(68));
  console.log("结论：" + passed + " / " + results.length + " 通过");
  console.log("=".repeat(68));
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => {
  console.error("走查脚本异常：", e);
  process.exit(2);
});
