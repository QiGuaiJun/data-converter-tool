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
  return { targetDbType: "mysql", connectionId: exportConnection.value };
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
  if (!connections.length) exportConnection.value = "__sqlite";
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
    extension: $("#exportExtension").value,
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
  if (!window.showDirectoryPicker) {
    setStatus("当前浏览器不支持直接选择文件夹，请导出后点击下载文件。", "warn");
    return;
  }
  exportDirectoryHandle = await window.showDirectoryPicker({ mode: "readwrite" });
  exportFileHandle = null;
  document.querySelector('input[name="exportTargetMode"][value="folder"]').checked = true;
  $("#exportFolder").value = exportDirectoryHandle.name;
  setStatus(`已选择导出文件夹：${exportDirectoryHandle.name}`, "success");
}

async function chooseFile() {
  if (!window.showSaveFilePicker) {
    setStatus("当前浏览器不支持直接选择目标文件，请导出后点击下载文件。", "warn");
    return;
  }
  const extension = $("#exportExtension").value || "xlsx";
  exportFileHandle = await window.showSaveFilePicker({
    suggestedName: ($("#outputName").value || "export").replace(/\.[^.]+$/, "") + `.${extension}`,
    types: [
      {
        description: "Export file",
        accept: { "application/octet-stream": [`.${extension}`] },
      },
    ],
  });
  exportDirectoryHandle = null;
  document.querySelector('input[name="exportTargetMode"][value="file"]').checked = true;
  $("#outputName").value = exportFileHandle.name.replace(/\.[^.]+$/, "");
  setStatus(`已选择目标文件：${exportFileHandle.name}`, "success");
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
  const defaultName = existingJob?.name || $("#outputName").value || $("#queryName")?.value || "导出任务";
  const taskName = prompt("请输入导出任务名称：", defaultName);
  if (!taskName) throw new Error("请填写任务名称。");
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
    document.querySelector("#newExportTask").addEventListener("click", () => saveExportTask().catch((error) => setStatus(error.message, "error")));
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

function renderExportTaskJobs() {
  ensureExportTaskPanel();
  const list = document.querySelector("#exportTaskList");
  const tree = document.querySelector("#exportTaskTree");
  const openButton = document.querySelector("#openExportTask");
  const saveButton = document.querySelector("#newExportTask");
  const deleteButton = document.querySelector("#deleteExportTask");
  if (!list) return;
  if (!exportTaskJobs.length) {
    list.className = "module-task-list empty";
    list.textContent = "暂无导出任务";
    if (tree) tree.textContent = "";
  } else {
    list.className = "module-task-list";
    list.innerHTML = exportTaskJobs
      .map((job) => `<button type="button" class="module-task-item ${job.id === selectedExportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}">${escapeHtml(job.name)}</button>`)
      .join("");
    if (tree) {
      tree.innerHTML = exportTaskJobs
        .map((job) => `<button type="button" class="tree-child ${job.id === selectedExportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}">${escapeHtml(job.name)}</button>`)
        .join("");
    }
  }
  const hasSelection = Boolean(selectedExportTaskId && exportTaskJobs.some((job) => job.id === selectedExportTaskId));
  if (openButton) openButton.disabled = !hasSelection;
  if (saveButton) saveButton.textContent = hasSelection ? "保存修改" : "新增导出";
  if (deleteButton) deleteButton.disabled = !hasSelection;
  document.querySelectorAll("#exportTaskList [data-id], #exportTaskTree [data-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedExportTaskId = button.dataset.id;
      renderExportTaskJobs();
    });
    button.addEventListener("dblclick", () => openSelectedExportTask().catch((error) => setStatus(error.message, "error")));
  });
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
  exportConnection.value = config?.targetDbType === "sqlite" ? "__sqlite" : config?.connectionId || exportConnection.value;

  const items = Array.isArray(config?.items) ? config.items : [];
  if (config?.sourceType === "table" || items.some((item) => item.type === "table")) {
    sourceMode = "table";
    await loadSources();
    const tableNames = new Set(items.map((item) => item.table || item.name).filter(Boolean));
    exportSourceList.querySelectorAll("input[type='checkbox']").forEach((input) => {
      input.checked = tableNames.has(input.value);
    });
  } else if (items.length > 1) {
    sourceMode = "multi";
    exportSql.value = items.map((item) => item.sql).filter(Boolean).join("; ");
    $("#queryName").value = items[0]?.name || $("#queryName").value || "query";
    exportSourceList.textContent = "当前使用多个 SQL 查询，使用分号分隔";
  } else {
    sourceMode = "query";
    exportSql.value = config?.sql || items[0]?.sql || exportSql.value;
    $("#queryName").value = items[0]?.name || $("#queryName").value || "query";
    exportSourceList.textContent = "当前使用单个 SQL 查询";
  }
}

async function openSelectedExportTask() {
  const job = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  if (!job) return;
  const step = exportTaskStep(job);
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
  $("#queryName").value = $("#queryName").value || "query";
  exportSql.value = exportSql.value || "select 1 as value";
});
$("#multiQuery").addEventListener("click", () => {
  sourceMode = "multi";
  exportSourceList.textContent = "当前使用多个 SQL 查询，使用分号分隔";
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
});
exportConnection.addEventListener("change", () => {
  sourceMode = "query";
  exportSourceList.textContent = "当前使用单个 SQL 查询";
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
loadConnections()
  .then(() => {
    sourceMode = "query";
    exportSourceList.textContent = "当前使用单个 SQL 查询";
    exportSql.value = exportSql.value || "select 1 as value";
    loadExportTaskJobs().catch((error) => setStatus(error.message, "error"));
    setStatus("已进入 SQL 查询导出模式。可直接预览或开始导出。", "success");
  })
  .catch((error) => setStatus(error.message, "error"));
