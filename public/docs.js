// 操作手册帮助中心前端（方案 B：任务化首页 + 侧栏树 + 正文）
// 服务端 /api/docs 返回目录索引；/api/docs?id=xxx 返回一篇文档渲染后的 HTML。
(function () {
  const navItems = [
    ["connections", "/connections.html"],
    ["import", "/"],
    ["export", "/export.html"],
    ["sync", "/sync.html"],
    ["query", "/query.html"],
    ["tables", "/tables.html"],
    ["jobs", "/jobs.html"],
    ["schedule", "/schedule.html"],
    ["api", "/api.html"],
    ["docs", "/docs.html"],
    ["feedback", "/feedback.html"],
  ];
  const labels = {
    import: { title: "导入", icon: "IN", color: "orange" },
    export: { title: "导出", icon: "OUT", color: "cyan" },
    connections: { title: "新建连接", icon: "DB", color: "green" },
    sync: { title: "同步", icon: "SYNC", color: "red" },
    query: { title: "查询", icon: "SQL", color: "dark" },
    tables: { title: "表", icon: "TAB", color: "blue" },
    jobs: { title: "作业", icon: "JOB", color: "teal" },
    schedule: { title: "定时任务", icon: "TIME", color: "purple" },
    api: { title: "API", icon: "API", color: "black" },
    docs: { title: "操作手册", icon: "DOC", color: "violet" },
    feedback: { title: "咨询建议反馈", icon: "?", color: "sky" },
  };

  // 通用的线性图标池
  const ICON = {
    start: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5-6-3.5z"/></svg>`,
    import: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v8"/><path d="m8 7 4-4 4 4"/><rect x="2" y="14" width="20" height="7" rx="1.6"/></svg>`,
    export: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4"/><path d="m8 8 4-4 4 4"/><rect x="2" y="14" width="20" height="7" rx="1.6"/></svg>`,
    query: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>`,
    help: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 1 1 3.7 2.4c-.9.4-1.2 1-1.2 1.9"/><path d="M12 17h.01"/></svg>`,
    db: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12v5c0 1.7 3.6 3 8 3s8-1.3 8-3v-5"/></svg>`,
    table: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><path d="M9 9v11"/></svg>`,
    jobs: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/></svg>`,
    clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
    sql: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2z"/><path d="M4 6v4c0 1.1 3.6 2 8 2s8-.9 8-2V6"/><path d="M4 10v4c0 1.1 3.6 2 8 2s8-.9 8-2v-4"/><path d="M4 14v4c0 1.1 3.6 2 8 2s8-.9 8-2v-4"/></svg>`,
  };

  // 欢迎页顶部：快捷入口（按图片文案）
  const quickEntries = [
    { title: "新手入门", desc: "5 分钟快速上手", doc: "user/quick-start", tone: "#2563eb", icon: ICON.start },
    { title: "功能指南", desc: "详细操作说明", doc: "user/import", tone: "#16a34a", icon: ICON.import },
    { title: "场景教程", desc: "典型场景案例", doc: "", tone: "#ea580c", icon: ICON.sql },
    { title: "常见问题", desc: "问题解决方案", doc: "user/faq", tone: "#7c3aed", icon: ICON.help },
    { title: "视频教程", desc: "观看教学视频", doc: "", tone: "#0d9488", icon: ICON.export },
  ];

  // 想完成什么？——操作流程（对标图片：圆形彩色图标 + 标题 + 描述，箭头串联）
  const flowSteps = [
    { label: "导入数据", desc: "从文件导入数据库", doc: "user/import", icon: ICON.import, tone: "#10b981" },
    { label: "导出数据", desc: "从数据库导出文件", doc: "user/export", icon: ICON.export, tone: "#2563eb" },
    { label: "查询数据", desc: "使用 SQL 查询数据", doc: "user/query", icon: ICON.sql, tone: "#8b5cf6" },
    { label: "创建作业", desc: "组合步骤处理数据", doc: "user/jobs", icon: ICON.jobs, tone: "#f59e0b" },
    { label: "定时执行", desc: "定时自动执行任务", doc: "user/schedule", icon: ICON.clock, tone: "#10b981" },
  ];

  // 功能模块卡片（含风格统一的缩略示意）
  const modules = [
    { n: 1, title: "数据库连接", doc: "user/connections", desc: "配置并管理 MySQL / SQLite 数据源，一处保存处处可用。", icon: ICON.db, c1: "#1e40af", c2: "#3b82f6", pts: ["新建 / 测试连接", "自动过滤系统库", "连接集中管理"] },
    { n: 2, title: "数据导入", doc: "user/import", desc: "把 Excel、CSV、TXT 等文件写入数据库表。", icon: ICON.import, c1: "#15803d", c2: "#22c55e", pts: ["字段映射与类型识别", "追加 / 更新等模式", "空白替换与去重"] },
    { n: 3, title: "数据导出", doc: "user/export", desc: "将数据库数据导出为 Excel、CSV 等文件。", icon: ICON.export, c1: "#0f766e", c2: "#14b8a6", pts: ["多格式导出", "自定义脚本", "结果校验"] },
    { n: 4, title: "数据查询", doc: "user/query", desc: "使用 SQL 编辑器快速查询数据库。", icon: ICON.sql, c1: "#0369a1", c2: "#0ea5e9", pts: ["SQL 编辑器", "结果表格展示", "常用查询示例"] },
    { n: 5, title: "数据表", doc: "user/tables", desc: "查看表结构、字段与类型，快速定位数据。", icon: ICON.table, c1: "#0e7490", c2: "#06b6d4", pts: ["表列表浏览", "结构与字段预览", "快速定位"] },
    { n: 6, title: "作业", doc: "user/jobs", desc: "把多个任务组合成一个可复用的处理流程。", icon: ICON.jobs, c1: "#6d28d9", c2: "#8b5cf6", pts: ["步骤编排", "执行条件", "日志查看"] },
    { n: 7, title: "定时任务", doc: "user/schedule", desc: "按计划自动执行作业或导入导出任务。", icon: ICON.clock, c1: "#c2410c", c2: "#f59e0b", pts: ["多种定时方式", "启用 / 暂停", "执行记录"] },
    { n: 8, title: "快速开始", doc: "user/quick-start", desc: "10 分钟走通「导入」到数据库的完整流程。", icon: ICON.start, c1: "#047857", c2: "#2dd4bf", pts: ["完整流程示例", "分步操作指引", "结果核对"] },
    { n: 9, title: "常见问题", doc: "user/faq", desc: "连接、导入、导出、作业等高频问题解答。", icon: ICON.help, c1: "#b91c1c", c2: "#f43f5e", pts: ["高频问题解答", "排障思路", "术语说明"] },
    { n: 10, title: "操作手册目录", doc: "", desc: "按「快速开始 / 功能模块 / 更多帮助」系统浏览全部指南。", icon: ICON.table, c1: "#334155", c2: "#64748b", pts: ["结构化目录导航", "一键返回总览", "全局搜索定位"] },
  ];

  // 侧栏目录分组（按图片复刻：五组 + 带图标；subs 为暂无文档的占位子项标题）
  const sideGroups = [
    { label: "快速开始", ids: ["user/quick-start"], icon: ICON.start },
    { label: "功能模块", ids: ["user/connections", "user/import", "user/export", "user/query", "user/tables", "user/jobs", "user/schedule"], icon: ICON.db },
    { label: "场景教程", ids: [], subs: ["常用场景", "进阶场景"], icon: ICON.query },
    { label: "场景资料", ids: ["user/faq"], icon: ICON.help },
    { label: "管理员手册", ids: [], subs: ["部署与安装", "系统配置", "备份与恢复", "安全与日志"], icon: ICON.table },
  ];

  // 顶部统计徽章（图片中位于导航栏右侧的数字徽章）
  const stats = [
    { n: () => modules.length, suffix: "+", label: "功能模块", tone: "#2563eb" },
    { n: () => indexData.index.length, suffix: "", label: "操作指南", tone: "#16a34a" },
    { n: () => 5, suffix: "", label: "核心流程", tone: "#ea580c" },
    { n: () => 1, suffix: "套", label: "常见问题合集", tone: "#7c3aed" },
  ];

  let indexData = { index: [], categories: {} };

  function ribbon(key) {
    return navItems
      .map(([id, href]) => {
        const item = labels[id];
        return `<a class="ribbon-item ${id === key ? "active" : ""}" href="${href}"><span class="ribbon-icon ${item.color}">${item.icon}</span><span>${item.title}</span></a>`;
      })
      .join("");
  }

  function docTitle(id) {
    const d = indexData.index.find((x) => x.id === id);
    return d ? d.title : id.split("/").pop();
  }

  // 文档 id -> 侧栏图标
  const docIcons = {
    "user/quick-start": ICON.start,
    "user/connections": ICON.db,
    "user/import": ICON.import,
    "user/export": ICON.export,
    "user/query": ICON.sql,
    "user/tables": ICON.table,
    "user/jobs": ICON.jobs,
    "user/schedule": ICON.clock,
    "user/faq": ICON.help,
  };

  function sidebarTree(activeId) {
    // 深色侧边栏：按图片五组分组渲染目录（组头带图标，组内项带图标，空组显示占位）
    return sideGroups
      .map((g) => {
        const head = `<div class="doc-tree-cat">${g.icon}<span>${escapeHtml(g.label)}</span></div>`;
        let items;
        if (g.ids.length) {
          items = g.ids
            .map((id) => `<a class="doc-tree-item ${id === activeId ? "active" : ""}" data-id="${id}">${docIcons[id] || ""}<span>${escapeHtml(docTitle(id))}</span></a>`)
            .join("");
        } else if (g.subs && g.subs.length) {
          items = g.subs
            .map((t) => `<a class="doc-tree-item soon" data-soon="1"><span class="doc-tree-dot"></span><span>${escapeHtml(t)}</span><em class="doc-tree-chip">敬请期待</em></a>`)
            .join("");
        } else {
          items = `<div class="doc-tree-empty">内容整理中，敬请期待</div>`;
        }
        return `<div class="doc-tree-group">${head}${items}</div>`;
      })
      .join("");
  }

  function key() {
    return "docs";
  }

  async function loadIndex() {
    const res = await fetch("/api/docs", { cache: "no-store" });
    const payload = await res.json();
    if (payload && payload.ok) {
      indexData = payload;
    }
  }

  function renderShell() {
    const statItems = stats
      .map((s) => `<div class="docs-stat" style="--tone:${s.tone}"><b>${s.n()}${s.suffix}</b><span>${s.label}</span></div>`)
      .join("");
    document.body.innerHTML = `
      <div class="docs-site">
        <header class="docs-topbar">
          <div class="docs-topbar-title">
            <b>数据导表工具操作手册</b>
            <span>帮助您快速掌握数据导入、导出、查询、作业与定时任务等核心功能</span>
          </div>
          <div class="docs-topbar-stats">${statItems}</div>
          <div class="docs-topbar-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
            <input id="docsSearchInput" type="text" placeholder="搜索操作手册" autocomplete="off" />
            <div class="docs-search-results" id="docsSearchResults"></div>
          </div>
        </header>
        <div class="docs-site-body">
          <aside class="docs-nav">
            <div class="docs-nav-brand">
              <span class="docs-brand-logo">M</span>
              <div class="docs-brand-name">
                <b>数据导表工具</b>
                <span>操作手册</span>
              </div>
            </div>
            <nav class="docs-tree">${sidebarTree("")}</nav>
            <div class="docs-nav-foot">
              <a class="docs-return" href="/"><span class="docs-return-ico">←</span> 返回主应用</a>
              <div class="docs-sidebar-version" id="appVersion">数据导表工具</div>
            </div>
          </aside>
          <main class="docs-workspace" id="docsMain"></main>
        </div>
      </div>`;
    // 侧栏版本号
    fetch("/api/meta", { cache: "no-store" })
      .then((r) => r.json())
      .then((p) => {
        if (p && p.ok && p.appVersion) {
          const el = document.querySelector("#appVersion");
          if (el) el.textContent = `v${p.appVersion}`;
        }
      })
      .catch(() => {});
    // 绑定文档树点击
    document.querySelectorAll(".doc-tree-item").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.dataset.id) openDoc(el.dataset.id);
        else showToast("内容整理中，敬请期待");
      });
    });
    // 搜索
    initSearch();
    // 首次展示：若带 ?doc= 参数打开对应文档；否则显示首页
    const params = new URLSearchParams(location.search);
    const initDoc = params.get("doc");
    if (initDoc) {
      openDoc(initDoc);
    } else {
      renderHome();
    }
  }

  // 轻量提示条
  function showToast(msg) {
    let t = document.querySelector("#docsToast");
    if (!t) {
      t = document.createElement("div");
      t.id = "docsToast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._tm);
    t._tm = setTimeout(() => t.classList.remove("show"), 2200);
  }

  function thumb(m) {
    // 迷你应用界面预览：浅色窗口 + 细工具栏 + 侧栏 + 数据行，主色仅作点缀（对标图片的专业缩略图）
    return `<div class="doc-thumb" style="--tc:${m.c1}">
      <div class="doc-thumb-toolbar">
        <span class="doc-thumb-dot"></span><span class="doc-thumb-dot"></span><span class="doc-thumb-dot"></span>
        <span class="doc-thumb-title"><i>${m.icon}</i>${escapeHtml(m.title)}</span>
      </div>
      <div class="doc-thumb-body">
        <div class="doc-thumb-side"><i>${m.icon}</i><span></span><span class="on"></span><span></span><span></span></div>
        <div class="doc-thumb-main">
          <span class="thm-bar w90"></span>
          <span class="thm-bar w70"></span>
          <div class="thm-table">
            <div class="thm-trow"><i class="tc"></i><span class="w60"></span><span class="w30"></span></div>
            <div class="thm-trow"><i class="tc"></i><span class="w55"></span><span class="w35"></span></div>
            <div class="thm-trow"><i class="tc"></i><span class="w65"></span><span class="w28"></span></div>
          </div>
          <span class="thm-pill">${escapeHtml(m.title)}</span>
        </div>
      </div>
    </div>`;
  }

  function renderHome() {
    const main = document.querySelector("#docsMain");
    const quick = quickEntries
      .map(
        (q) => `<button type="button" class="docs-quick" data-doc="${q.doc}" data-soon="${q.doc ? "" : "1"}" style="--tone:${q.tone}">
          <span class="docs-quick-ico">${q.icon}</span>
          <span class="docs-quick-txt"><b>${escapeHtml(q.title)}</b><span>${escapeHtml(q.desc)}</span></span>
        </button>`
      )
      .join("");
    const flow = flowSteps
      .map(
        (s) => `<button type="button" class="docs-flow-step" data-doc="${s.doc}" style="--tone:${s.tone}">
          <span class="docs-flow-ico">${s.icon}</span>
          <span class="docs-flow-txt"><b>${escapeHtml(s.label)}</b><span>${escapeHtml(s.desc)}</span></span>
        </button>`
      )
      .join('<span class="docs-flow-arrow">→</span>');
    const mods = modules
      .map(
        (m) => `<article class="docs-module-card">
          <div class="docs-module-head"><span class="docs-module-no" style="--tc:${m.c1}">${m.n}</span><h3>${escapeHtml(m.title)}</h3></div>
          <p class="docs-module-desc">${escapeHtml(m.desc)}</p>
          <button type="button" class="doc-thumb-link" data-doc="${m.doc}">${thumb(m)}</button>
          <ul class="docs-module-pts">${m.pts.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>
          <a class="docs-module-link" data-doc="${m.doc}">${m.doc ? "查看详细指南 →" : "进入总览 →"}</a>
        </article>`
      )
      .join("");
    main.innerHTML = `
      <div class="docs-main-inner">
        <section class="docs-welcome">
          <h1>欢迎使用数据导表工具操作手册 🎉</h1>
          <p>导入、导出、查询、作业、定时任务 —— 全部能力一目了然，点卡片直达对应指南。</p>
          <div class="docs-quick-grid">${quick}</div>
        </section>
        <section class="docs-flow-card">
          <h2>我想完成什么？</h2>
          <div class="docs-flow">${flow}</div>
        </section>
        <section class="docs-modules">
          <h2 class="docs-section-title">功能模块 · 10 大模块全面覆盖</h2>
          <div class="docs-module-grid">${mods}</div>
        </section>
        <footer class="docs-footer">
          <div class="docs-footer-links">
            <a href="/feedback.html">提交反馈</a><a href="/#contacts">联系我们</a><a href="/docs.html">在线支持</a><a href="/docs.html">视频教程</a>
          </div>
          <div class="docs-footer-meta">文档版本：v1.0.0 · 最后更新：2026-09-10</div>
        </footer>
      </div>`;
    main.querySelectorAll("[data-doc]").forEach((el) => {
      el.addEventListener("click", () => {
        if (el.dataset.soon === "1") {
          showToast("内容整理中，敬请期待");
          return;
        }
        if (el.dataset.doc) openDoc(el.dataset.doc);
        else renderHome();
      });
    });
  }

  // 搜索：按标题过滤文档，实时下拉提示，点击打开
  function initSearch() {
    const input = document.querySelector("#docsSearchInput");
    const results = document.querySelector("#docsSearchResults");
    if (!input || !results) return;
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      if (!q) {
        results.innerHTML = "";
        results.classList.remove("open");
        return;
      }
      const hits = (indexData.index || [])
        .filter((d) => (d.title || "").toLowerCase().includes(q) || d.id.toLowerCase().includes(q))
        .slice(0, 8);
      results.innerHTML = hits.length
        ? hits.map((d) => `<button type="button" class="docs-search-hit" data-doc="${d.id}">${escapeHtml(d.title)}<span>${escapeHtml(d.id)}</span></button>`).join("")
        : `<div class="docs-search-empty">没有匹配的文档</div>`;
      results.classList.toggle("open", true);
    });
    input.addEventListener("blur", () => setTimeout(() => results.classList.remove("open"), 150));
    results.addEventListener("click", (ev) => {
      const hit = ev.target.closest("[data-doc]");
      if (hit) {
        results.classList.remove("open");
        input.value = "";
        openDoc(hit.dataset.doc);
      }
    });
  }

  async function openDoc(docId) {
    const main = document.querySelector("#docsMain");
    main.innerHTML = `<div class="docs-loading">加载中…</div>`;
    try {
      const res = await fetch(`/api/docs?id=${encodeURIComponent(docId)}`, { cache: "no-store" });
      const payload = await res.json();
      if (!payload.ok) {
        main.innerHTML = `<div class="docs-error">${escapeHtml(payload.error || "文档不存在。")}</div>`;
        return;
      }
      renderArticle(payload.title, payload.html);
      markActive(docId);
    } catch (error) {
      main.innerHTML = `<div class="docs-error">加载文档失败。</div>`;
    }
  }

  function renderArticle(title, html) {
    const main = document.querySelector("#docsMain");
    main.innerHTML = `
      <article class="docs-article">
        <header class="docs-article-header">
          <a href="/docs.html" class="docs-back">← 返回帮助中心</a>
          <h1>${escapeHtml(title)}</h1>
        </header>
        <nav class="docs-table-of-contents" id="docsToc"></nav>
        <div class="docs-body" id="docsBody">${html}</div>
      </article>`;
    buildToc();
  }

  // 基于正文的 h2/h3 自动生成目录，并给标题加锚点
  function buildToc() {
    const body = document.querySelector("#docsBody");
    if (!body) return;
    const toc = document.querySelector("#docsToc");
    if (!toc) return;
    const links = [];
    let index = 0;
    body.querySelectorAll("h2, h3").forEach((heading) => {
      if (heading.querySelector("a.docs-back")) return;
      const id = `sec-${index++}`;
      heading.id = id;
      const level = heading.tagName === "H3" ? "h3" : "h2";
      links.push(`<a class="toc-${level}" href="#${id}">${escapeHtml(heading.textContent)}</a>`);
    });
    if (links.length) {
      toc.innerHTML = `<div class="docs-toc-label">本章目录</div>` + links.join("");
    }
  }

  function markActive(docId) {
    document.querySelectorAll(".doc-tree-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.id === docId);
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  (async function init() {
    await loadIndex();
    renderShell();
  })();
})();