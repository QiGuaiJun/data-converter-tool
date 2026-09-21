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
    ? connections.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name || item.id)}</option>`).join("")
    : '<option value="">暂无数据库连接</option>';
}

// 取所选连接真实的 dbType，取不到时回退 mysql（与 export.js 的 connectionPayload 保持一致）
function selectedDbType(selectId) {
  const connectionId = $(selectId)?.value || "";
  const item = connections.find((connection) => connection.id === connectionId);
  return item?.dbType || "mysql";
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

function renderResultSet(container, resultSet) {
  const columns = (resultSet && resultSet.columns) || [];
  const rows = (resultSet && resultSet.rows) || [];
  if (!columns.length) {
    container.className = "table-wrap empty";
    container.textContent = "该结果集没有字段";
    return;
  }
  container.className = "table-wrap";
  container.innerHTML = `<table><thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

// 每条语句一句话结论：结果集 / 结构变更 / 影响行数 / 失败原因
function statementSummaryText(item) {
  if (item.error) return `失败：${item.error}`;
  if (item.kind === "resultset") {
    const sets = item.resultSets || [];
    const rows = sets.reduce((sum, set) => sum + (set.rowCount || 0), 0);
    return `返回 ${sets.length} 个结果集 / ${rows} 行`;
  }
  if (item.kind === "ddl") return "结构变更成功";
  return `影响 ${item.affectedRows ?? 0} 行`;
}

function renderExecution(result) {
  const statements = result.statements || [];
  const sets = [];
  statements.forEach((item) => {
    (item.resultSets || []).forEach((set) => {
      sets.push({ ...set, label: `结果集 ${sets.length + 1}`, statementIndex: item.index });
    });
  });

  const summary = $("#queryStatementSummary");
  if (statements.length) {
    summary.classList.remove("hidden");
    summary.innerHTML = statements
      .map((item) => {
        const preview = String(item.sql || "").split("\n")[0].slice(0, 100);
        const warn = (item.warnings || []).length ? ` · 警告 ${item.warnings.length}` : "";
        return (
          `<div class="query-statement-row${item.error ? " error" : ""}">` +
          `<span class="query-statement-index">${item.index}</span>` +
          `<code>${escapeHtml(preview)}</code>` +
          `<span class="query-statement-meta">${escapeHtml(statementSummaryText(item))} · ${item.elapsedMs} ms${warn}</span>` +
          `</div>`
        );
      })
      .join("");
  } else {
    summary.classList.add("hidden");
    summary.innerHTML = "";
  }

  const tabs = $("#queryResultTabs");
  const container = $("#queryResult");
  if (sets.length > 1) {
    tabs.classList.remove("hidden");
    tabs.innerHTML = sets
      .map((set, index) => `<button type="button" class="query-tab${index === 0 ? " active" : ""}" data-set="${index}">${escapeHtml(set.label)}（${set.rowCount} 行）</button>`)
      .join("");
    tabs.querySelectorAll("[data-set]").forEach((button) => {
      button.addEventListener("click", () => {
        tabs.querySelectorAll(".query-tab").forEach((item) => item.classList.toggle("active", item === button));
        renderResultSet(container, sets[Number(button.dataset.set)]);
      });
    });
  } else {
    tabs.classList.add("hidden");
    tabs.innerHTML = "";
  }

  if (sets.length) {
    renderResultSet(container, sets[0]);
  } else {
    container.className = "table-wrap empty";
    container.textContent = result.failedIndex ? "执行失败，没有结果集" : "语句执行完成，没有返回结果集（DDL / DML 结果见上方摘要）";
  }

  const totals = result.totals || {};
  const meta = [
    `${totals.statements ?? statements.length} 条语句`,
    `结果集 ${totals.resultSets ?? sets.length}`,
    `影响 ${totals.affectedRows ?? 0} 行`,
    `${result.elapsedMs} ms`,
  ];
  if (result.truncated) meta.push("仅显示前 1000 行");
  if (result.failedIndex) meta.push(`第 ${result.failedIndex} 条失败`);
  $("#queryResultMeta").textContent = meta.join(" · ");

  const warnings = statements.flatMap((item) => (item.warnings || []).map((text) => `第 ${item.index} 条：${text}`));
  if (result.failedIndex) {
    const failed = statements.find((item) => item.index === result.failedIndex);
    setStatus(`执行失败：${(failed && failed.error) || result.message}`, "error");
  } else if (warnings.length) {
    setStatus(warnings.slice(0, 3).join("；"), "error");
  } else {
    setStatus(result.message || "执行成功", "success");
  }
}

// 有选中文本时只执行选中部分（便于在大脚本里单独跑一条）
function selectedSql() {
  const editor = $("#querySql");
  const value = editor.value || "";
  const start = editor.selectionStart ?? 0;
  const end = editor.selectionEnd ?? 0;
  if (end > start && value.slice(start, end).trim()) return value.slice(start, end);
  return value;
}

async function postQuery(payload) {
  return requestJson("/api/query/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function confirmLines(result) {
  const lines = ["以下语句被判定为高危，确认后才会真正执行："];
  (result.dangerous || []).forEach((item) => {
    lines.push(`第 ${item.index} 条：${item.preview}`);
    (item.reasons || []).forEach((reason) => lines.push(`　· ${reason}`));
  });
  lines.push("执行后无法通过本工具撤销，请确认目标库和条件无误。");
  return lines;
}

async function runQuery() {
  if (!$("#queryConnection").value) throw new Error("请先保存并选择数据库连接。");
  const button = $("#runQuery");
  button.disabled = true;
  setStatus("正在执行...");
  try {
    const payload = {
      connectionId: $("#queryConnection").value,
      targetDbType: selectedDbType("#queryConnection"),
      sql: selectedSql(),
    };
    let result = await postQuery(payload);
    if (result.needConfirm) {
      const ok = await window.dcConfirm({
        title: "高危 SQL 确认",
        lines: confirmLines(result),
        okText: "确认执行",
      });
      if (!ok) {
        $("#queryResultMeta").textContent = "已取消执行";
        setStatus("已取消（未执行任何语句）", "");
        return;
      }
      result = await postQuery({ ...payload, confirmToken: result.confirmToken });
      if (result.needConfirm) throw new Error("确认令牌已失效，请重新执行。");
    }
    renderExecution(result);
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
  if (!selectedQueryId || !confirm("确定删除当前保存的查询吗？\n若该查询已被定时任务引用，关联作业会一并删除。")) return;
  const result = await requestJson(`/api/queries?id=${encodeURIComponent(selectedQueryId)}`, { method: "DELETE" });
  selectedQueryId = "";
  await loadSavedQueries();
  const note = result?.removedJobs ? `（已联动删除 ${result.removedJobs} 个定时作业）` : "";
  setStatus(`查询已删除${note}`, "success");
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
