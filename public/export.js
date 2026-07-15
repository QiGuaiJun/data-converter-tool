const exportConnection = document.querySelector("#exportConnection");
const exportSourceList = document.querySelector("#exportSourceList");
const exportSql = document.querySelector("#exportSql");
const exportStatus = document.querySelector("#exportStatus");
const exportPreviewMeta = document.querySelector("#exportPreviewMeta");
const exportPreviewTable = document.querySelector("#exportPreviewTable");
const exportResultMeta = document.querySelector("#exportResultMeta");
const exportResults = document.querySelector("#exportResults");
const sqlFileInput = document.querySelector("#sqlFileInput");
const chooseExportFolder = document.querySelector("#chooseExportFolder");
const chooseExportFile = document.querySelector("#chooseExportFile");
const previewExportButton = document.querySelector("#previewExport");
const runExportButton = document.querySelector("#runExport");
const startExportButton = document.querySelector("#startExport");

let connections = [];
let sources = [];
let sourceMode = "query";
let exportDirectoryHandle = null;
let exportFileHandle = null;
let exportTaskJobs = [];
let selectedExportTaskId = "";
let exportEditorVisible = false;

function $(selector) {
  return document.querySelector(selector);
}

function radioValue(name) {
  return document.querySelector(`input[name="${name}"]:checked`)?.value || "";
}

function setRadioValue(name, value) {
  const input = document.querySelector(`input[name="${name}"][value="${CSS.escape(String(value || ""))}"]`);
  if (input) input.checked = true;
}

function setControlValue(id, value) {
  const input = document.querySelector(`#${CSS.escape(id)}`);
  if (!input || value === undefined || value === null) return;
  if (input.type === "checkbox") {
    input.checked = String(value) === "true" || value === true;
  } else {
    input.value = value;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(message, type = "") {
  exportStatus.textContent = message;
  exportStatus.className = type;
}

function setExportEditorVisible(visible) {
  exportEditorVisible = Boolean(visible);
  document.querySelector(".export-shell")?.classList.toggle("task-overview-mode", !exportEditorVisible);
  document.body.classList.toggle("export-task-overview", !exportEditorVisible);
  document.body.classList.toggle("export-task-editor", exportEditorVisible);
}

function resetExportEditor() {
  document.querySelectorAll(".export-left input, .export-left select, .export-left textarea, .export-right input, .export-right select, .export-right textarea").forEach((control) => {
    if (control.type === "checkbox" || control.type === "radio") control.checked = control.defaultChecked;
    else if (control.tagName === "SELECT") control.selectedIndex = 0;
    else control.value = control.defaultValue;
  });
  sourceMode = "query";
  exportSql.value = "select 1 as value";
  exportSourceList.textContent = "当前使用单个 SQL 查询";
  exportSourceList.classList.add("hidden");
  exportPreviewMeta.textContent = "暂无预览";
  exportPreviewTable.className = "table-wrap empty";
  exportPreviewTable.textContent = "配置导出对象后可预览";
  exportResultMeta.textContent = "暂无导出结果";
  exportResults.textContent = "暂无导出文件";
  exportDirectoryHandle = null;
  exportFileHandle = null;
}

function startNewExportTask() {
  selectedExportTaskId = "";
  updateExportTaskSelection();
  resetExportEditor();
  setExportEditorVisible(true);
  setStatus("已新建导出任务，请配置导出内容和选项。", "success");
}

function applyPendingQueryExport() {
  const sql = sessionStorage.getItem("pendingExportSql");
  if (!sql) return false;
  const name = sessionStorage.getItem("pendingExportName") || "query";
  sessionStorage.removeItem("pendingExportSql");
  sessionStorage.removeItem("pendingExportName");
  resetExportEditor();
  sourceMode = "query";
  exportSql.value = sql;
  $("#queryName").value = name;
  $("#exportFileName").value = name;
  exportSourceList.classList.add("hidden");
  setExportEditorVisible(true);
  setStatus("已从 SQL 查询模块带入查询，可继续配置导出选项。", "success");
  return true;
}

function fileNameFromPath(path) {
  return String(path || "").split(/[\\/]/).pop();
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "请求失败。");
  }
  return payload;
}

function connectionPayload() {
  if (exportConnection.value === "__sqlite") {
    return { targetDbType: "sqlite", connectionId: "" };
  }
  const item = connections.find((connection) => connection.id === exportConnection.value);
  const payload = { targetDbType: item?.dbType || "mysql", connectionId: exportConnection.value };
  if (item) {
    payload.dbHost = item.host || "";
    payload.dbPort = String(item.port || "");
    payload.dbUser = item.user || "";
    payload.dbName = item.database || "";
    payload.dbCharset = item.charset || "utf8mb4";
    payload.sslEnabled = item.sslEnabled ? "true" : "false";
  }
  return payload;
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
  exportConnection.innerHTML = "";
  for (const item of connections) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.name} (${item.host}/${item.database})`;
    exportConnection.append(option);
  }
  const sqlite = document.createElement("option");
  sqlite.value = "__sqlite";
  sqlite.textContent = "本地 SQLite";
  exportConnection.append(sqlite);
  exportConnection.value = connections[0]?.id || "__sqlite";
  if (!connections.length) {
    setStatus("当前没有保存的数据库连接。导出业务表前，请先到“新建连接”保存 MySQL 连接。", "warn");
  }
}

async function loadSources() {
  setStatus("正在读取导出对象...");
  const params = new URLSearchParams(connectionPayload());
  const payload = await requestJson(`/api/export/sources?${params.toString()}`);
  sources = payload.sources || [];
  renderSources();
  setStatus(`已读取 ${sources.length} 个对象。`, "success");
}

function renderSources() {
  sourceMode = "table";
  exportSql.value = "";
  exportSourceList.classList.remove("hidden");
  if (!sources.length) {
    exportSourceList.textContent = "当前连接没有可导出的表";
    return;
  }
  exportSourceList.innerHTML = sources
    .map(
      (item, index) => `
        <label class="export-source-item">
          <input type="checkbox" value="${escapeHtml(item.name)}" ${index === 0 ? "checked" : ""} />
          <span>${escapeHtml(item.name)}</span>
          <small>${escapeHtml(item.type || "")}${item.rows ? ` · ${item.rows}` : ""}</small>
        </label>`,
    )
    .join("");
}

function selectedItems() {
  const queryName = ($("#queryName")?.value || "query").trim() || "query";
  if (sourceMode === "query") {
    const sql = exportSql.value.trim();
    if (!sql) throw new Error("请填写查询 SQL。");
    return [{ type: "query", name: queryName, sql }];
  }
  if (sourceMode === "multi") {
    const parts = exportSql.value
      .split(";")
      .map((item) => item.trim())
      .filter(Boolean);
    if (!parts.length) throw new Error("请填写多个查询 SQL。");
    return parts.map((sql, index) => ({ type: "query", name: `${queryName}_${index + 1}`, sql }));
  }
  const checked = [...exportSourceList.querySelectorAll("input[type='checkbox']:checked")].map((item) => item.value);
  if (!checked.length) throw new Error("请选择至少一张表。");
  return checked.map((name) => ({ type: "table", table: name, name }));
}

function collectPayload() {
  const items = selectedItems();
  return {
    ...connectionPayload(),
    items,
    sourceType: sourceMode === "table" ? "table" : "query",
    table: items[0]?.table || "",
    sql: sourceMode === "table" ? "" : exportSql.value.trim(),
    queryName: ($("#queryName")?.value || "query").trim() || "query",
    sourceMode,
    extension: $("#exportExtension").value,
    exportFolder: $("#exportFolder").value,
    exportFileName: $("#exportFileName").value.trim(),
    outputName: $("#outputName").value,
    sheetName: $("#sheetName").value,
    headerMode: radioValue("headerMode"),
    exportMode: radioValue("exportMode"),
    skipEmptyTable: $("#skipEmptyTable").checked ? "true" : "false",
    commentAsFileName: $("#commentAsFileName").checked ? "true" : "false",
    splitField: $("#splitField").value,
    splitIntoFolder: $("#splitIntoFolder").checked ? "true" : "false",
    splitNameWithField: $("#splitNameWithField").checked ? "true" : "false",
    exportFields: $("#exportFields").value,
    whereClause: $("#whereClause").value,
    exportTimeField: $("#exportTimeField").value,
    filePrefix: $("#filePrefix").value,
    fileSuffix: $("#fileSuffix").value,
    batchRows: $("#batchRows").value,
    splitByBatch: $("#splitByBatch").checked ? "true" : "false",
    encoding: $("#encoding").value,
    delimiter: $("#delimiter").value,
    lineDelimiter: $("#lineDelimiter").value,
    openFileAfterExport: $("#openFileAfterExport").checked ? "true" : "false",
    openFolderAfterExport: $("#openFolderAfterExport").checked ? "true" : "false",
    exportRemark: $("#exportRemark").value,
    activeExportTab: document.querySelector("[data-export-tab].active")?.dataset.exportTab || "data",
    rowHeight: $("#rowHeight").value,
    columnWidth: $("#columnWidth").value,
    fontName: $("#fontName").value,
    fontSize: $("#fontSize").value,
    lockedColumns: $("#lockedColumns").value,
    addBorder: $("#addBorder").checked ? "true" : "false",
    lockHeader: $("#lockHeader").checked ? "true" : "false",
    clearLogBeforeExport: $("#clearLogBeforeExport").checked ? "true" : "false",
    beforeSql: $("#beforeSql").value,
    afterSql: $("#afterSql").value,
  };
}

function renderTable(container, columns, rows) {
  if (!columns.length) {
    container.className = "table-wrap empty";
    container.textContent = "暂无数据";
    return;
  }
  container.className = "table-wrap";
  const thead = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
  const tbody = rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("");
  container.innerHTML = `<table><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`;
}

async function chooseFolder() {
  const result = await requestJson("/api/export/choose-target", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "folder" }),
  });
  if (!result.path) return;
  exportDirectoryHandle = null;
  exportFileHandle = null;
  document.querySelector('input[name="exportTargetMode"][value="folder"]').checked = true;
  $("#exportFolder").value = result.path;
  setStatus(`已选择导出文件夹：${result.path}`, "success");
}

async function chooseFile() {
  const extension = $("#exportExtension").value || "xlsx";
  const suggestedName = ($("#outputName").value || "export").replace(/\.[^.]+$/, "") + `.${extension}`;
  const result = await requestJson("/api/export/choose-target", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind: "file", extension, suggestedName }),
  });
  if (!result.path) return;
  exportDirectoryHandle = null;
  exportFileHandle = null;
  document.querySelector('input[name="exportTargetMode"][value="file"]').checked = true;
  $("#outputName").value = result.path;
  setStatus(`已选择目标文件：${result.path}`, "success");
}

async function copyExportedFiles(result) {
  if (!result.files?.length) return { copied: [], errors: [] };
  const copied = [];
  const errors = [];
  const targetMode = radioValue("exportTargetMode");

  if (targetMode === "file" && exportFileHandle) {
    try {
      const response = await fetch(result.downloadUrls[0]);
      const blob = await response.blob();
      const writable = await exportFileHandle.createWritable({ keepExistingData: false });
      await writable.write(blob);
      await writable.close();
      copied.push({ index: 0, name: exportFileHandle.name });
    } catch (error) {
      exportFileHandle = null;
      errors.push(`目标文件写入失败，请重新选择文件：${error.message}`);
    }
    return { copied, errors };
  }

  if (targetMode === "folder" && exportDirectoryHandle) {
    for (let index = 0; index < result.files.length; index += 1) {
      const name = fileNameFromPath(result.files[index]);
      try {
        const response = await fetch(result.downloadUrls[index]);
        const blob = await response.blob();
        try {
          await exportDirectoryHandle.removeEntry(name);
        } catch (_) {
          // The file may not exist; overwriting through a fresh handle is still fine.
        }
        const handle = await exportDirectoryHandle.getFileHandle(name, { create: true });
        const writable = await handle.createWritable({ keepExistingData: false });
        await writable.write(blob);
        await writable.close();
        copied.push({ index, name });
      } catch (error) {
        errors.push(`${name} 复制到选择文件夹失败，请重新选择文件夹或点击下载：${error.message}`);
      }
    }
  }
  if (errors.length) {
    exportDirectoryHandle = null;
  }
  return { copied, errors };
}

async function previewExport() {
  previewExportButton.disabled = true;
  try {
    setStatus("正在预览...");
    const payload = collectPayload();
    payload.items = undefined;
    const result = await requestJson("/api/export/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    exportPreviewMeta.textContent = `${result.sourceName} · ${result.rows.length} 行 · ${result.columns.length} 列`;
    renderTable(exportPreviewTable, result.columns, result.rows);
    setStatus("预览完成。", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    previewExportButton.disabled = false;
  }
}

async function runExport() {
  runExportButton.disabled = true;
  startExportButton.disabled = true;
  previewExportButton.disabled = true;
  try {
    setStatus("正在导出...");
    const result = await requestJson("/api/export/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    const copyResult = await copyExportedFiles(result);
    const copiedByIndex = new Map(copyResult.copied.map((item) => [item.index, item.name]));
    exportResultMeta.textContent = `${result.files.length} 个文件 · ${result.rows} 行 · ${result.elapsedMs} ms`;
    exportResults.innerHTML = result.files.length
      ? result.files
          .map((file, index) => {
            const url = result.downloadUrls[index];
            const copiedName = copiedByIndex.get(index);
            const copied = copiedName ? `<div>已复制到选择的位置：${escapeHtml(copiedName)}</div>` : "";
            return `<div class="log-item success"><strong>${escapeHtml(fileNameFromPath(file))}<span>成功</span></strong><div>${escapeHtml(file)}</div>${copied}<a href="${escapeHtml(url)}">下载文件</a></div>`;
          })
          .join("")
      : "没有生成文件";
    if (copyResult.errors.length) {
      exportResults.innerHTML += copyResult.errors.map((message) => `<div class="log-item failed">${escapeHtml(message)}</div>`).join("");
      setStatus(`${result.message} 文件已生成，但复制到所选位置失败，请点击下载文件或重新选择文件夹。`, "warn");
    } else {
      setStatus(copyResult.copied.length ? `${result.message} 已复制到你选择的位置。` : result.message, "success");
    }
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    runExportButton.disabled = false;
    startExportButton.disabled = false;
    previewExportButton.disabled = false;
  }
}

async function saveExportTask() {
  const existingJob = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  const existingStep = existingJob ? exportTaskStep(existingJob) : null;
  const config = collectPayload();
  const defaultName = existingJob?.name || $("#exportFileName").value || $("#outputName").value || "导出任务";
  const taskName = defaultName.trim() || "导出任务";
  const payload = {
    id: existingJob?.id,
    name: taskName,
    enabled: true,
    steps: [
      {
        id: existingStep?.id || crypto.randomUUID(),
        name: taskName,
        type: "export",
        enabled: true,
        continueOnError: existingStep?.continueOnError || false,
        config,
      },
    ],
  };
  const result = await requestJson("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  selectedExportTaskId = result.job.id;
  await loadExportTaskJobs();
  setStatus(`${existingJob ? "已更新" : "已保存"}导出任务：${result.job.name}，可在定时任务中调用。`, "success");
  return result.job;
}

function isExportTaskJob(job) {
  return (job.steps || [])[0]?.type === "export";
}

function exportTaskStep(job) {
  return (job.steps || []).find((step) => step.type === "export") || {};
}

function ensureExportTaskPanel() {
  const shell = document.querySelector(".export-shell");
  if (shell && !document.querySelector("#exportTaskPanel")) {
    const panel = document.createElement("section");
    panel.id = "exportTaskPanel";
    panel.className = "module-task-panel";
    panel.innerHTML = `
      <div class="module-task-toolbar">
        <button id="openExportTask" type="button" disabled>打开导出</button>
        <button id="newExportTask" type="button">新增导出</button>
        <button id="deleteExportTask" type="button" disabled>删除导出</button>
        <span id="exportTaskHint">当前模块保存的导出任务</span>
      </div>
      <div id="exportTaskList" class="module-task-list empty">暂无导出任务</div>`;
    shell.insertAdjacentElement("afterbegin", panel);
    document.querySelector("#openExportTask").addEventListener("click", () => openSelectedExportTask().catch((error) => setStatus(error.message, "error")));
    document.querySelector("#newExportTask").addEventListener("click", startNewExportTask);
    document.querySelector("#deleteExportTask").addEventListener("click", () => deleteSelectedExportTask().catch((error) => setStatus(error.message, "error")));
  }

  const activeNode = document.querySelector(".module-tree .tree-node.active");
  if (activeNode && !document.querySelector("#exportTaskTree")) {
    const tree = document.createElement("div");
    tree.id = "exportTaskTree";
    tree.className = "module-task-tree";
    activeNode.insertAdjacentElement("afterend", tree);
  }
}

function updateExportTaskSelection() {
  const hasSelection = Boolean(selectedExportTaskId && exportTaskJobs.some((job) => job.id === selectedExportTaskId));
  document.querySelectorAll("#exportTaskList [data-id], #exportTaskTree [data-id]").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === selectedExportTaskId);
  });
  const openButton = document.querySelector("#openExportTask");
  const newButton = document.querySelector("#newExportTask");
  const deleteButton = document.querySelector("#deleteExportTask");
  if (openButton) openButton.disabled = !hasSelection;
  if (newButton) newButton.textContent = "新增导出";
  if (deleteButton) deleteButton.disabled = !hasSelection;
}

function renderExportTaskJobs() {
  ensureExportTaskPanel();
  const list = document.querySelector("#exportTaskList");
  const tree = document.querySelector("#exportTaskTree");
  if (!list) return;
  if (!exportTaskJobs.length) {
    list.className = "module-task-list empty";
    list.textContent = "暂无导出任务";
    if (tree) tree.textContent = "";
  } else {
    list.className = "module-task-list";
    list.innerHTML = exportTaskJobs
      .map((job) => `<button type="button" class="module-task-item task-export ${job.id === selectedExportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><span class="task-type-icon">OUT</span><span class="task-item-name">${escapeHtml(job.name)}</span></button>`)
      .join("");
    if (tree) {
      tree.innerHTML = exportTaskJobs
        .map((job) => `<button type="button" class="tree-child task-export ${job.id === selectedExportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><span class="task-type-icon">OUT</span><span class="task-item-name">${escapeHtml(job.name)}</span></button>`)
        .join("");
    }
  }
  document.querySelectorAll("#exportTaskList [data-id], #exportTaskTree [data-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedExportTaskId = button.dataset.id;
      updateExportTaskSelection();
    });
    button.addEventListener("dblclick", () => {
      selectedExportTaskId = button.dataset.id;
      updateExportTaskSelection();
      openSelectedExportTask().catch((error) => setStatus(error.message, "error"));
    });
  });
  updateExportTaskSelection();
}

async function loadExportTaskJobs() {
  ensureExportTaskPanel();
  const payload = await requestJson("/api/jobs");
  exportTaskJobs = (payload.jobs || []).filter(isExportTaskJob);
  if (selectedExportTaskId && !exportTaskJobs.some((job) => job.id === selectedExportTaskId)) {
    selectedExportTaskId = "";
  }
  renderExportTaskJobs();
}

async function applyExportTaskConfig(config) {
  for (const [key, value] of Object.entries(config || {})) {
    setControlValue(key, value);
  }
  for (const name of ["headerMode", "exportMode", "exportTargetMode"]) {
    if (config?.[name]) setRadioValue(name, config[name]);
  }
  if (config?.targetDbType && config.targetDbType !== "sqlite" && config?.connectionId && !connections.some((item) => item.id === config.connectionId)) {
    const option = document.createElement("option");
    option.value = config.connectionId;
    option.textContent = config.dbHost ? `保存的连接快照 (${config.dbHost}/${config.dbName || ""})` : "连接已丢失，请重新选择";
    exportConnection.prepend(option);
    if (config.dbHost) {
      connections.push({
        id: config.connectionId,
        dbType: config.targetDbType,
        host: config.dbHost,
        port: config.dbPort,
        user: config.dbUser,
        database: config.dbName,
        charset: config.dbCharset || "utf8mb4",
        sslEnabled: config.sslEnabled === "true",
      });
    }
  }
  exportConnection.value = config?.targetDbType === "sqlite" ? "__sqlite" : config?.connectionId || exportConnection.value;
  if (config?.targetDbType && config.targetDbType !== "sqlite" && exportConnection.value === "__sqlite") {
    setStatus("这个导出任务原来使用 MySQL，但当前没有找到保存的连接。请先新建连接，然后重新保存导出任务。", "error");
  }

  const items = Array.isArray(config?.items) ? config.items : [];
  if (config?.sourceType === "table" || items.some((item) => item.type === "table")) {
    sourceMode = "table";
    await loadSources();
    const tableNames = new Set(items.map((item) => item.table || item.name).filter(Boolean));
    exportSourceList.querySelectorAll("input[type='checkbox']").forEach((input) => {
      input.checked = tableNames.has(input.value);
    });
  } else if (config?.sourceMode === "multi" || items.length > 1) {
    sourceMode = "multi";
    exportSql.value = items.map((item) => item.sql).filter(Boolean).join("; ");
    $("#queryName").value = config?.queryName || items[0]?.name || $("#queryName").value || "query";
    exportSourceList.textContent = "当前使用多个 SQL 查询，使用分号分隔";
    exportSourceList.classList.add("hidden");
  } else {
    sourceMode = "query";
    exportSql.value = config?.sql || items[0]?.sql || exportSql.value;
    $("#queryName").value = config?.queryName || items[0]?.name || $("#queryName").value || "query";
    exportSourceList.textContent = "当前使用单个 SQL 查询";
    exportSourceList.classList.add("hidden");
  }

  const activeTab = config?.activeExportTab || "data";
  document.querySelectorAll("[data-export-tab]").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.exportTab === activeTab);
  });
  document.querySelectorAll("[data-export-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.exportPanel === activeTab);
  });
}

async function openSelectedExportTask() {
  const job = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  if (!job) return;
  const step = exportTaskStep(job);
  resetExportEditor();
  setExportEditorVisible(true);
  await applyExportTaskConfig(step.config || {});
  setStatus(`已打开导出任务：${job.name}`, "success");
}

async function deleteSelectedExportTask() {
  const job = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  if (!job) return;
  if (!window.confirm(`确定删除导出任务“${job.name}”吗？关联的定时任务也会一起删除。`)) return;
  await requestJson(`/api/jobs?id=${encodeURIComponent(job.id)}`, { method: "DELETE" });
  selectedExportTaskId = "";
  await loadExportTaskJobs();
  setExportEditorVisible(false);
  setStatus("已删除导出任务。", "success");
}

document.querySelectorAll("[data-export-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-export-tab]").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll("[data-export-panel]").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`[data-export-panel="${tab.dataset.exportTab}"]`).classList.add("active");
  });
});

$("#chooseTables").addEventListener("click", () => loadSources().catch((error) => setStatus(error.message, "error")));
$("#singleQuery").addEventListener("click", () => {
  sourceMode = "query";
  exportSourceList.textContent = "当前使用单个 SQL 查询";
  exportSourceList.classList.add("hidden");
  $("#queryName").value = $("#queryName").value || "query";
  exportSql.value = exportSql.value || "select 1 as value";
});
$("#multiQuery").addEventListener("click", () => {
  sourceMode = "multi";
  exportSourceList.textContent = "当前使用多个 SQL 查询，使用分号分隔";
  exportSourceList.classList.add("hidden");
  $("#queryName").value = $("#queryName").value || "query";
  exportSql.value = exportSql.value || "select 1 as value; select 2 as value";
});
sqlFileInput.addEventListener("change", async () => {
  const file = sqlFileInput.files?.[0];
  if (!file) return;
  sourceMode = "query";
  exportSql.value = await file.text();
  if (!$("#queryName").value || $("#queryName").value === "query") {
    $("#queryName").value = file.name.replace(/\.[^.]+$/, "") || "query";
  }
  exportSourceList.textContent = `已读取 SQL 文件：${file.name}`;
  exportSourceList.classList.add("hidden");
});
exportConnection.addEventListener("change", () => {
  sourceMode = "query";
  exportSourceList.textContent = "当前使用单个 SQL 查询";
  exportSourceList.classList.add("hidden");
  exportSql.value = exportSql.value || "select 1 as value";
  setStatus("已切换连接。可直接编写 SQL，或点击“选择表”读取表列表。", "success");
});
chooseExportFolder.addEventListener("click", () => chooseFolder().catch((error) => setStatus(error.message, "error")));
chooseExportFile.addEventListener("click", () => chooseFile().catch((error) => setStatus(error.message, "error")));
$("#previewExport").addEventListener("click", previewExport);
$("#runExport").addEventListener("click", runExport);
$("#startExport").addEventListener("click", runExport);
$("#stopExport").addEventListener("click", () => setStatus("当前导出任务为同步执行，暂无运行中的任务。", "warn"));
$("#saveExportConfig").addEventListener("click", () => saveExportTask().catch((error) => setStatus(error.message, "error")));
$("#explainExport").addEventListener("click", () => setStatus("不支持的 .xls、DBF 和系统自动打开文件夹已在页面禁用。", "warn"));

ensureExportTaskPanel();
setExportEditorVisible(false);
loadConnections()
  .then(() => {
    sourceMode = "query";
    exportSourceList.textContent = "当前使用单个 SQL 查询";
    exportSourceList.classList.add("hidden");
    exportSql.value = exportSql.value || "select 1 as value";
    loadExportTaskJobs().catch((error) => setStatus(error.message, "error"));
    if (!applyPendingQueryExport()) setStatus("已进入 SQL 查询导出模式。可直接预览或开始导出。", "success");
  })
  .catch((error) => setStatus(error.message, "error"));
