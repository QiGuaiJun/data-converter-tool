const $ = (selector) => document.querySelector(selector);
let connections = [];
let tables = [];
let selectedTable = "";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || "请求失败");
  return payload;
}

function setStatus(message, type = "") {
  $("#tableStatus").textContent = message;
  $("#tableStatus").className = type;
}

function connectionParams() {
  return new URLSearchParams({ targetDbType: "mysql", connectionId: $("#tableConnection").value });
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
  $("#tableConnection").innerHTML = connections.length
    ? connections.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (${escapeHtml(item.host)}/${escapeHtml(item.database)})</option>`).join("")
    : '<option value="">暂无数据库连接</option>';
}

function renderTables() {
  const keyword = $("#tableSearch").value.trim().toLowerCase();
  const filtered = tables.filter((item) => !keyword || item.name.toLowerCase().includes(keyword) || String(item.comment || "").toLowerCase().includes(keyword));
  $("#tableCount").textContent = `${filtered.length} 个`;
  const list = $("#tableList");
  list.className = filtered.length ? "tables-list" : "tables-list empty";
  list.innerHTML = filtered.length
    ? filtered.map((item) => `<button type="button" class="table-list-item ${item.name === selectedTable ? "active" : ""}" data-name="${escapeHtml(item.name)}"><span class="table-type-icon">${item.type === "VIEW" ? "VIEW" : "TAB"}</span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.comment || "无注释")}</small></span><em>${Number(item.rows || 0).toLocaleString()} 行</em></button>`).join("")
    : "没有匹配的表";
  list.querySelectorAll("[data-name]").forEach((button) => button.addEventListener("click", () => selectTable(button.dataset.name)));
}

async function loadTables() {
  if (!$("#tableConnection").value) {
    tables = [];
    renderTables();
    setStatus("请先保存数据库连接", "error");
    return;
  }
  setStatus("正在读取数据库表...");
  const payload = await requestJson(`/api/export/sources?${connectionParams()}`);
  tables = payload.sources || [];
  if (selectedTable && !tables.some((item) => item.name === selectedTable)) selectedTable = "";
  renderTables();
  setStatus(`已读取 ${tables.length} 个数据库对象`, "success");
}

function renderGrid(container, columns, rows) {
  if (!columns.length) {
    container.className = "table-wrap empty";
    container.textContent = "暂无数据";
    return;
  }
  container.className = "table-wrap";
  container.innerHTML = `<table><thead><tr>${columns.map((name) => `<th>${escapeHtml(name)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function renderColumns(columns) {
  const rows = columns.map((item) => [item.position, item.name, item.type, item.key || "", item.nullable ? "是" : "否", item.default, item.extra, item.comment]);
  renderGrid($("#tableColumns"), ["序号", "字段名", "数据类型", "键", "允许空", "默认值", "附加属性", "注释"], rows);
}

async function selectTable(name) {
  selectedTable = name;
  renderTables();
  $("#openInQuery").disabled = false;
  $("#openInExport").disabled = false;
  setStatus(`正在读取 ${name}...`);
  try {
    const params = connectionParams();
    params.set("name", name);
    const payload = await requestJson(`/api/target-table-details?${params}`);
    const table = payload.table;
    $("#selectedTableName").textContent = table.name;
    $("#selectedTableMeta").textContent = `${table.type}${table.comment ? ` · ${table.comment}` : ""} · ${table.columns.length} 个字段`;
    renderColumns(table.columns || []);
    renderGrid($("#tablePreview"), table.previewColumns || [], table.previewRows || []);
    $("#tableDdl").textContent = table.ddl || "暂无 DDL";
    setStatus(`已读取 ${name}`, "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function activateTab(name) {
  document.querySelectorAll("[data-table-tab]").forEach((tab) => tab.classList.toggle("active", tab.dataset.tableTab === name));
  document.querySelectorAll("[data-table-view]").forEach((view) => view.classList.toggle("active", view.dataset.tableView === name));
}

function quoteTable(name) {
  return `\`${String(name).replaceAll("`", "``")}\``;
}

function openInQuery() {
  if (!selectedTable) return;
  sessionStorage.setItem("pendingQuerySql", `SELECT * FROM ${quoteTable(selectedTable)} LIMIT 100`);
  sessionStorage.setItem("pendingQueryName", `${selectedTable} 查询`);
  sessionStorage.setItem("pendingQueryConnection", $("#tableConnection").value);
  location.href = "/query.html";
}

function openInExport() {
  if (!selectedTable) return;
  sessionStorage.setItem("pendingExportSql", `SELECT * FROM ${quoteTable(selectedTable)}`);
  sessionStorage.setItem("pendingExportName", selectedTable);
  location.href = "/export.html";
}

$("#refreshTables").addEventListener("click", () => loadTables().catch((error) => setStatus(error.message, "error")));
$("#tableConnection").addEventListener("change", () => { selectedTable = ""; loadTables().catch((error) => setStatus(error.message, "error")); });
$("#tableSearch").addEventListener("input", renderTables);
$("#openInQuery").addEventListener("click", openInQuery);
$("#openInExport").addEventListener("click", openInExport);
document.querySelectorAll("[data-table-tab]").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tableTab)));

loadConnections().then(loadTables).catch((error) => setStatus(error.message, "error"));
