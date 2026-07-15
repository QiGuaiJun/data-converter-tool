const $ = (selector) => document.querySelector(selector);
let connections = [];
let savedQueries = [];
let selectedQueryId = "";

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
  $("#queryStatus").textContent = message;
  $("#queryStatus").className = type;
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
  $("#queryConnection").innerHTML = connections.length
    ? connections.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (${escapeHtml(item.host)}/${escapeHtml(item.database)})</option>`).join("")
    : '<option value="">暂无数据库连接</option>';
}

function renderSavedQueries() {
  const list = $("#savedQueryList");
  list.className = savedQueries.length ? "query-list" : "query-list empty";
  list.innerHTML = savedQueries.length
    ? savedQueries.map((item) => `<button type="button" class="query-list-item ${item.id === selectedQueryId ? "active" : ""}" data-id="${escapeHtml(item.id)}"><span class="task-type-icon">SQL</span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.updatedAt)}</small></span></button>`).join("")
    : "暂无保存的查询";
  list.querySelectorAll("[data-id]").forEach((button) => button.addEventListener("click", () => openSavedQuery(button.dataset.id)));
  $("#deleteQuery").disabled = !selectedQueryId;
}

async function loadSavedQueries() {
  const payload = await requestJson("/api/queries");
  savedQueries = payload.queries || [];
  if (selectedQueryId && !savedQueries.some((item) => item.id === selectedQueryId)) selectedQueryId = "";
  renderSavedQueries();
}

function openSavedQuery(id) {
  const item = savedQueries.find((query) => query.id === id);
  if (!item) return;
  selectedQueryId = id;
  $("#queryName").value = item.name;
  $("#querySql").value = item.sql;
  if (item.connectionId && connections.some((connection) => connection.id === item.connectionId)) $("#queryConnection").value = item.connectionId;
  renderSavedQueries();
  setStatus(`已打开：${item.name}`, "success");
}

function renderResult(columns, rows) {
  const container = $("#queryResult");
  if (!columns.length) {
    container.className = "table-wrap empty";
    container.textContent = "查询没有返回字段";
    return;
  }
  container.className = "table-wrap";
  container.innerHTML = `<table><thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

async function runQuery() {
  if (!$("#queryConnection").value) throw new Error("请先保存并选择数据库连接。");
  const button = $("#runQuery");
  button.disabled = true;
  setStatus("正在执行查询...");
  try {
    const result = await requestJson("/api/query/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ connectionId: $("#queryConnection").value, targetDbType: "mysql", sql: $("#querySql").value }),
    });
    renderResult(result.columns || [], result.rows || []);
    $("#queryResultMeta").textContent = `${result.rowCount} 行 · ${result.elapsedMs} ms${result.truncated ? " · 仅显示前 1000 行" : ""}`;
    setStatus("查询执行成功", "success");
  } catch (error) {
    $("#queryResultMeta").textContent = "执行失败";
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function saveQuery() {
  const name = $("#queryName").value.trim();
  if (!name) throw new Error("请填写查询名称。");
  const payload = await requestJson("/api/queries", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: selectedQueryId || undefined, name, connectionId: $("#queryConnection").value, sql: $("#querySql").value }),
  });
  selectedQueryId = payload.query.id;
  await loadSavedQueries();
  setStatus("查询已保存", "success");
}

async function deleteQuery() {
  if (!selectedQueryId || !confirm("确定删除当前保存的查询吗？")) return;
  await requestJson(`/api/queries?id=${encodeURIComponent(selectedQueryId)}`, { method: "DELETE" });
  selectedQueryId = "";
  await loadSavedQueries();
  setStatus("查询已删除", "success");
}

function newQuery() {
  selectedQueryId = "";
  $("#queryName").value = "";
  $("#querySql").value = "SELECT 1 AS value";
  renderSavedQueries();
  setStatus("已新建查询");
}

function applyPendingTableQuery() {
  const sql = sessionStorage.getItem("pendingQuerySql");
  if (!sql) return;
  $("#querySql").value = sql;
  $("#queryName").value = sessionStorage.getItem("pendingQueryName") || "表查询";
  const connectionId = sessionStorage.getItem("pendingQueryConnection") || "";
  if (connectionId && connections.some((item) => item.id === connectionId)) $("#queryConnection").value = connectionId;
  sessionStorage.removeItem("pendingQuerySql");
  sessionStorage.removeItem("pendingQueryName");
  sessionStorage.removeItem("pendingQueryConnection");
  setStatus("已从表模块生成查询", "success");
}

function sendToExport() {
  sessionStorage.setItem("pendingExportSql", $("#querySql").value);
  sessionStorage.setItem("pendingExportName", $("#queryName").value || "query");
  location.href = "/export.html";
}

$("#runQuery").addEventListener("click", runQuery);
$("#newQuery").addEventListener("click", newQuery);
$("#saveQuery").addEventListener("click", () => saveQuery().catch((error) => setStatus(error.message, "error")));
$("#deleteQuery").addEventListener("click", () => deleteQuery().catch((error) => setStatus(error.message, "error")));
$("#refreshQueries").addEventListener("click", () => loadSavedQueries().catch((error) => setStatus(error.message, "error")));
$("#sendToExport").addEventListener("click", sendToExport);
$("#querySql").addEventListener("keydown", (event) => { if (event.ctrlKey && event.key === "Enter") { event.preventDefault(); runQuery(); } });

Promise.all([loadConnections(), loadSavedQueries()]).then(applyPendingTableQuery).catch((error) => setStatus(error.message, "error"));
