const modules = {
  connections: { title: "新建连接", icon: "DB", color: "green", body: "管理数据库连接、测试连接、保存连接分组。当前连接弹窗仍保留在导入页，下一步可迁移到此独立页面。" },
  sync: { title: "同步", icon: "SYNC", color: "red", body: "配置数据库到数据库同步任务，包含源库、目标库、字段映射、同步模式和执行日志。" },
  query: { title: "查询", icon: "SQL", color: "dark", body: "执行 SQL 查询、保存常用查询、查看结果，并可把查询结果交给导出模块。" },
  tables: { title: "表", icon: "TAB", color: "blue", body: "查看数据库表、字段、行数、DDL 和数据预览。" },
  jobs: { title: "作业", icon: "JOB", color: "teal", body: "组合导入、导出、同步、查询子任务，配置执行顺序和失败策略。" },
  schedule: { title: "定时任务", icon: "TIME", color: "purple", body: "按一次性、间隔、每日、每周、每月计划执行作业。" },
  api: { title: "API", icon: "API", color: "black", body: "预留命令行/API 调用能力，便于桌面工具被外部脚本调度。" },
  docs: { title: "操作手册", icon: "DOC", color: "violet", body: "集中放置导入、导出、同步、连接、定时任务的操作说明。" },
  feedback: { title: "咨询建议反馈", icon: "?", color: "sky", body: "记录使用问题、改进建议和待办需求。" },
};

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
  ...modules,
};

function renderModulePage() {
  const key = document.body.dataset.module;
  const current = modules[key] || modules.connections;
  const ribbon = navItems
    .map(([id, href]) => {
      const item = labels[id];
      return `<a class="ribbon-item ${id === key ? "active" : ""}" href="${href}"><span class="ribbon-icon ${item.color}">${item.icon}</span><span>${item.title}</span></a>`;
    })
    .join("");
  const tree = navItems
    .slice(0, 8)
    .map(([id, href]) => {
      const item = labels[id];
      return `<a class="tree-node ${id === key ? "active" : ""}" href="${href}"><span>${item.icon}</span> ${item.title}</a>`;
    })
    .join("");
  document.body.innerHTML = `
    <div class="desktop-shell">
      <header class="app-ribbon">${ribbon}</header>
      <div class="desktop-main">
        <aside class="app-sidebar">
          <div class="sidebar-title">功能</div>
          <nav class="module-tree">${tree}</nav>
        </aside>
        <section class="app-workspace">
          <main class="module-placeholder">
            <header class="module-header">
              <span class="ribbon-icon ${current.color}">${current.icon}</span>
              <div>
                <h1>${current.title}</h1>
                <p>${current.body}</p>
              </div>
            </header>
            <section class="module-board">
              <div class="module-card">
                <strong>模块状态</strong>
                <p>此模块已拆分为独立页面，当前阶段保留入口与布局，功能开发时不会再挤占导入/导出页面。</p>
              </div>
              <div class="module-card">
                <strong>下一步</strong>
                <p>按业务优先级把具体配置区、执行区、日志区接入本页。</p>
              </div>
            </section>
          </main>
        </section>
      </div>
    </div>`;
}

renderModulePage();
