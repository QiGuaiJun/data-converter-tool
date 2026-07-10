const fileInput = document.querySelector("#fileInput");
const dirInput = document.querySelector("#dirInput");
const fileList = document.querySelector("#fileList");
const tableName = document.querySelector("#tableName");
const targetTableOptions = document.querySelector("#targetTableOptions");
const importForm = document.querySelector("#importForm");
const importButton = importForm.querySelector(".primary");
const statusBox = document.querySelector("#status");
const previewButton = document.querySelector("#previewButton");
const mappingDialog = document.querySelector("#mappingDialog");
const openMapping = document.querySelector("#openMapping");
const closeMapping = document.querySelector("#closeMapping");
const mappingTable = document.querySelector("#mappingTable");
const previewTable = document.querySelector("#previewTable");
const previewMeta = document.querySelector("#previewMeta");
const tables = document.querySelector("#tables");
const tableMeta = document.querySelector("#tableMeta");
const tablePreview = document.querySelector("#tablePreview");
const selectedTableMeta = document.querySelector("#selectedTableMeta");
const logs = document.querySelector("#logs");
const connectionSelect = document.querySelector("#connectionSelect");
const newConnection = document.querySelector("#newConnection");
const refreshConnections = document.querySelector("#refreshConnections");
const deleteConnection = document.querySelector("#deleteConnection");
const connectionDialog = document.querySelector("#connectionDialog");
const closeConnection = document.querySelector("#closeConnection");
const cancelConnection = document.querySelector("#cancelConnection");
const testConnection = document.querySelector("#testConnection");
const saveConnection = document.querySelector("#saveConnection");
const connectionStatus = document.querySelector("#connectionStatus");

let selectedFiles = [];
let currentColumns = [];
let savedConnections = [];
let importTaskJobs = [];
let selectedImportTaskId = "";

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

function setStatus(message, type = "") {
  statusBox.textContent = message;
  statusBox.className = type;
}

function setFiles(files) {
  selectedFiles = [...files].filter((file) => /\.(csv|txt|xlsx|xlsm|xls|json|xml|dbf)$/i.test(file.name));
  currentColumns = [];
  renderMapping([]);
  previewMeta.textContent = "暂无预览";
  previewTable.className = "table-wrap empty";
  previewTable.textContent = "选择文件后可预览前 20 行";
  if (!selectedFiles.length) {
    fileList.textContent = "尚未选择文件";
    setStatus("等待选择文件");
    return;
  }
  fileList.innerHTML = selectedFiles
    .map((file, index) => `<div class="file-item"><span>${index + 1}</span>${escapeHtml(file.webkitRelativePath || file.name)}</div>`)
    .join("");
  if (!tableName.value && selectedFiles.length === 1) {
    tableName.value = selectedFiles[0].name.replace(/\.[^.]+$/, "");
  }
  setStatus(`已选择 ${selectedFiles.length} 个文件，可以预览或导入。`, "success");
}

function buildFormData(includeAllFiles = true) {
  if (!selectedFiles.length) {
    throw new Error("请先选择文件。");
  }
  const data = new FormData();
  for (const file of includeAllFiles ? selectedFiles : [selectedFiles[0]]) {
    data.append("file", file);
  }

  data.append("tableName", radioValue("targetMode") === "manual" ? tableName.value : "");
  data.append("connectionId", connectionSelect?.value || "");
  data.append("importMode", radioValue("importMode"));
  data.append("hasHeader", "true");
  data.append("matchBy", $("#matchBy").value);

  for (const id of [
    "sheetFilterMode",
    "sheetName",
    "columnFilter",
    "headerRow",
    "dataStartRow",
    "importRowCount",
    "skipTailRows",
    "encoding",
    "delimiter",
    "lineDelimiter",
    "batchRows",
    "rowTag",
    "excelPassword",
    "dbHost",
    "dbPort",
    "dbName",
    "dbUser",
    "dbPassword",
    "dbCharset",
    "blankCellValues",
    "removeText",
    "dedupeColumns",
    "fillDownColumns",
    "dateColumns",
    "replaceBlankWith",
    "replaceTextFrom",
    "replaceTextTo",
    "customSql",
    "tableNameRule",
    "tableRegex",
    "tablePrefix",
    "tableSuffix",
    "duplicateTableMode",
    "fieldReplaceFrom",
    "fieldReplaceTo",
    "autoPkField",
    "importTimeField",
    "sheetNameExtract",
    "sheetNameField",
    "fixedValue",
    "fixedValueField",
    "beforeAllSql",
    "afterEachSql",
    "afterAllSql",
    "afterQuerySql",
    "afterQueryExport",
  ]) {
    data.append(id, $(`#${id}`).value);
  }

  for (const id of [
    "recursiveDir",
    "skipSeenFile",
    "sheetModeAll",
    "resumeImport",
    "trimValues",
    "deleteEmptyRows",
    "defaultForEmpty",
    "zeroForNumber",
    "emptyAsNull",
    "symbolToUnderscore",
    "tablePinyin",
    "fieldPinyin",
    "autoExpand",
    "disableLog",
    "clearLogBeforeImport",
    "deleteAfterSuccess",
  ]) {
    data.append(id, $(`#${id}`).checked ? "true" : "false");
  }

  data.append("tableCase", radioValue("tableCase"));
  data.append("fieldCase", radioValue("fieldCase"));
  data.append("targetDbType", radioValue("targetDbType"));
  data.append("sheetMode", $("#sheetModeAll").checked ? "all" : "specified");
  data.append("extraColumnMode", radioValue("extraColumnMode"));
  data.append("writeMode", radioValue("writeMode"));
  data.append("commitMode", radioValue("commitMode"));
  data.append("mapping", JSON.stringify(readMapping()));
  return data;
}

function formDataToImportConfig(data) {
  const config = {};
  for (const [key, value] of data.entries()) {
    if (key !== "file") config[key] = String(value);
  }
  return config;
}

async function saveImportTask() {
  const existingJob = importTaskJobs.find((item) => item.id === selectedImportTaskId);
  const existingStep = existingJob ? importTaskStep(existingJob) : null;
  const existingConfig = existingStep?.config || {};
  const data = buildFormData(false);
  const config = formDataToImportConfig(data);
  const defaultPath = existingConfig.path || selectedFiles[0]?.webkitRelativePath || selectedFiles[0]?.name || "";
  const path = prompt("请输入后台定时执行时可访问的本机文件或目录路径：", defaultPath);
  if (!path) throw new Error("保存任务需要填写文件或目录路径。");
  const taskName = prompt("请输入导入任务名称：", existingJob?.name || tableName.value || selectedFiles[0]?.name?.replace(/\.[^.]+$/, "") || "导入任务");
  if (!taskName) throw new Error("请填写任务名称。");
  config.path = path;
  const payload = {
    id: existingJob?.id,
    name: taskName,
    enabled: true,
    steps: [
      {
        id: existingStep?.id || crypto.randomUUID(),
        name: taskName,
        type: "import",
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
  selectedImportTaskId = result.job.id;
  await loadImportTaskJobs();
  setStatus(`${existingJob ? "已更新" : "已保存"}导入任务：${result.job.name}，可在定时任务中调用。`, "success");
  return result.job;
}

function ensureImportTaskButton() {
  if (document.querySelector("#saveImportTask")) return;
  const button = document.createElement("button");
  button.id = "saveImportTask";
  button.type = "button";
  button.textContent = "保存为任务";
  button.addEventListener("click", () => saveImportTask().catch((error) => setStatus(error.message, "error")));
  previewButton.insertAdjacentElement("afterend", button);
}

function isImportTaskJob(job) {
  return (job.steps || [])[0]?.type === "import";
}

function importTaskStep(job) {
  return (job.steps || []).find((step) => step.type === "import") || {};
}

function ensureImportTaskPanel() {
  const shell = document.querySelector(".import-shell");
  if (shell && !document.querySelector("#importTaskPanel")) {
    const panel = document.createElement("section");
    panel.id = "importTaskPanel";
    panel.className = "module-task-panel";
    panel.innerHTML = `
      <div class="module-task-toolbar">
        <button id="openImportTask" type="button" disabled>打开导入</button>
        <button id="newImportTask" type="button">新增导入</button>
        <button id="deleteImportTask" type="button" disabled>删除导入</button>
        <span id="importTaskHint">当前模块保存的导入任务</span>
      </div>
      <div id="importTaskList" class="module-task-list empty">暂无导入任务</div>`;
    shell.insertAdjacentElement("afterbegin", panel);
    document.querySelector("#openImportTask").addEventListener("click", openSelectedImportTask);
    document.querySelector("#newImportTask").addEventListener("click", () => saveImportTask().catch((error) => setStatus(error.message, "error")));
    document.querySelector("#deleteImportTask").addEventListener("click", deleteSelectedImportTask);
  }

  const activeNode = document.querySelector(".module-tree .tree-node.active");
  if (activeNode && !document.querySelector("#importTaskTree")) {
    const tree = document.createElement("div");
    tree.id = "importTaskTree";
    tree.className = "module-task-tree";
    activeNode.insertAdjacentElement("afterend", tree);
  }
}

function updateImportTaskSelection() {
  const hasSelection = Boolean(selectedImportTaskId && importTaskJobs.some((job) => job.id === selectedImportTaskId));
  document.querySelectorAll("#importTaskList [data-id], #importTaskTree [data-id]").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === selectedImportTaskId);
  });
  const openButton = document.querySelector("#openImportTask");
  const saveButton = document.querySelector("#newImportTask");
  const deleteButton = document.querySelector("#deleteImportTask");
  if (openButton) openButton.disabled = !hasSelection;
  if (saveButton) saveButton.textContent = hasSelection ? "保存修改" : "新增导入";
  if (deleteButton) deleteButton.disabled = !hasSelection;
}

function renderImportTaskJobs() {
  ensureImportTaskPanel();
  const list = document.querySelector("#importTaskList");
  const tree = document.querySelector("#importTaskTree");
  if (!list) return;
  if (!importTaskJobs.length) {
    list.className = "module-task-list empty";
    list.textContent = "暂无导入任务";
    if (tree) tree.textContent = "";
  } else {
    list.className = "module-task-list";
    list.innerHTML = importTaskJobs
      .map((job) => `<button type="button" class="module-task-item ${job.id === selectedImportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}">${escapeHtml(job.name)}</button>`)
      .join("");
    if (tree) {
      tree.innerHTML = importTaskJobs
        .map((job) => `<button type="button" class="tree-child ${job.id === selectedImportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}">${escapeHtml(job.name)}</button>`)
        .join("");
    }
  }
  document.querySelectorAll("#importTaskList [data-id], #importTaskTree [data-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedImportTaskId = button.dataset.id;
      updateImportTaskSelection();
    });
    button.addEventListener("dblclick", () => {
      selectedImportTaskId = button.dataset.id;
      updateImportTaskSelection();
      openSelectedImportTask();
    });
  });
  updateImportTaskSelection();
}

async function loadImportTaskJobs() {
  ensureImportTaskPanel();
  const payload = await requestJson("/api/jobs");
  importTaskJobs = (payload.jobs || []).filter(isImportTaskJob);
  if (selectedImportTaskId && !importTaskJobs.some((job) => job.id === selectedImportTaskId)) {
    selectedImportTaskId = "";
  }
  renderImportTaskJobs();
}

function applyImportTaskConfig(config) {
  for (const [key, value] of Object.entries(config || {})) {
    setControlValue(key, value);
  }
  for (const name of ["targetMode", "importMode", "tableCase", "fieldCase", "targetDbType", "extraColumnMode", "writeMode", "commitMode"]) {
    if (config?.[name]) setRadioValue(name, config[name]);
  }
  if (config?.connectionId && connectionSelect) {
    connectionSelect.value = config.connectionId;
  }
  if (config?.mapping) {
    try {
      const mapping = JSON.parse(config.mapping);
      currentColumns = mapping.map((item) => item.source).filter(Boolean);
      renderMapping(currentColumns);
      [...mappingTable.querySelectorAll("tbody tr")].forEach((row, index) => {
        const item = mapping[index] || {};
        row.querySelector(".map-enabled").checked = item.enabled !== false;
        row.querySelector(".map-key").checked = Boolean(item.matchKey);
        row.querySelector(".map-target").value = item.target || item.source || "";
        row.querySelector(".map-default").value = item.defaultValue || "";
      });
    } catch (_) {
      renderMapping([]);
    }
  }
  loadTargetTableOptions().catch((error) => setStatus(error.message, "error"));
}

function openSelectedImportTask() {
  const job = importTaskJobs.find((item) => item.id === selectedImportTaskId);
  if (!job) return;
  const step = importTaskStep(job);
  applyImportTaskConfig(step.config || {});
  setStatus(`已打开导入任务：${job.name}${step.config?.path ? `，定时执行路径：${step.config.path}` : ""}`, "success");
}

async function deleteSelectedImportTask() {
  const job = importTaskJobs.find((item) => item.id === selectedImportTaskId);
  if (!job) return;
  if (!window.confirm(`确定删除导入任务“${job.name}”吗？关联的定时任务也会一起删除。`)) return;
  await requestJson(`/api/jobs?id=${encodeURIComponent(job.id)}`, { method: "DELETE" });
  selectedImportTaskId = "";
  await loadImportTaskJobs();
  setStatus("已删除导入任务。", "success");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "请求失败。");
  }
  return payload;
}

function setConnectionStatus(message, type = "") {
  connectionStatus.textContent = message;
  connectionStatus.className = `connection-status ${type}`.trim();
}

function connectionPayload() {
  return {
    id: $("#connId").value,
    name: $("#connName").value,
    dbType: "mysql",
    host: $("#connHost").value,
    port: $("#connPort").value,
    user: $("#connUser").value,
    password: $("#connPassword").value,
    database: $("#connDatabase").value,
    charset: $("#connCharset").value,
    sslEnabled: $("#connSslEnabled").checked,
    sslCa: $("#connSslCa").value,
    sslCert: $("#connSslCert").value,
    sslKey: $("#connSslKey").value,
  };
}

function fillConnectionDialog(connection = {}) {
  $("#connId").value = connection.id || "";
  $("#connName").value = connection.name || "";
  $("#connHost").value = connection.host || $("#dbHost").value || "192.168.1.102";
  $("#connPort").value = connection.port || $("#dbPort").value || "3306";
  $("#connUser").value = connection.user || $("#dbUser").value || "root";
  $("#connPassword").value = connection.id ? "" : connection.password || $("#dbPassword").value || "123456";
  const database = connection.database || $("#dbName").value || "lcdp_SR";
  $("#connDatabase").innerHTML = `<option value="${escapeHtml(database)}">${escapeHtml(database)}</option>`;
  $("#connDatabase").value = database;
  $("#connCharset").value = connection.charset || $("#dbCharset").value || "utf8mb4";
  $("#connSslEnabled").checked = Boolean(connection.sslEnabled);
  $("#connSslCa").value = connection.sslCa || "";
  $("#connSslCert").value = connection.sslCert || "";
  $("#connSslKey").value = connection.sslKey || "";
  setConnectionStatus("等待测试连接");
}

function applyConnection(connection) {
  if (!connection) return;
  document.querySelector('input[name="targetDbType"][value="mysql"]').checked = true;
  $("#dbHost").value = connection.host || "";
  $("#dbPort").value = connection.port || "3306";
  $("#dbName").value = connection.database || "";
  $("#dbUser").value = connection.user || "";
  $("#dbCharset").value = connection.charset || "utf8mb4";
}

function openConnectionDialog(connection = {}) {
  fillConnectionDialog(connection);
  connectionDialog.classList.remove("hidden");
}

function closeConnectionDialog() {
  connectionDialog.classList.add("hidden");
}

async function loadConnections(selectedId = connectionSelect.value) {
  const payload = await requestJson("/api/connections");
  savedConnections = payload.connections || [];
  connectionSelect.innerHTML = '<option value="">使用默认连接参数</option>';
  for (const item of savedConnections) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.name} (${item.host}${item.database ? `/${item.database}` : ""})`;
    connectionSelect.append(option);
  }
  if (selectedId && savedConnections.some((item) => item.id === selectedId)) {
    connectionSelect.value = selectedId;
  } else if (!selectedId && savedConnections.length) {
    connectionSelect.value = savedConnections[0].id;
  }
  const current = savedConnections.find((item) => item.id === connectionSelect.value);
  if (current) applyConnection(current);
  await loadTargetTableOptions();
}

function connectionParams() {
  const params = new URLSearchParams();
  params.set("targetDbType", radioValue("targetDbType"));
  params.set("connectionId", connectionSelect?.value || "");
  for (const id of ["dbHost", "dbPort", "dbName", "dbUser", "dbPassword", "dbCharset"]) {
    params.set(id, $(`#${id}`).value);
  }
  return params;
}

async function loadTargetTableOptions() {
  try {
    const payload = await requestJson(`/api/target-tables?${connectionParams().toString()}`);
    targetTableOptions.innerHTML = "";
    for (const item of payload.tables || []) {
      const option = document.createElement("option");
      option.value = item;
      targetTableOptions.append(option);
    }
  } catch (error) {
    targetTableOptions.innerHTML = "";
  }
}

function useManualTargetTable() {
  document.querySelector('input[name="targetMode"][value="manual"]').checked = true;
}

async function testCurrentConnection() {
  try {
    setConnectionStatus("正在测试连接...");
    const payload = await requestJson("/api/connections/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(connectionPayload()),
    });
    const selected = $("#connDatabase").value;
    $("#connDatabase").innerHTML = payload.databases
      .map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`)
      .join("");
    if (selected && payload.databases.includes(selected)) {
      $("#connDatabase").value = selected;
    } else if (payload.databases.includes("lcdp_SR")) {
      $("#connDatabase").value = "lcdp_SR";
    }
    setConnectionStatus(`连接成功，MySQL ${payload.version}`, "success");
  } catch (error) {
    setConnectionStatus(error.message, "error");
  }
}

async function saveCurrentConnection() {
  try {
    setConnectionStatus("正在保存连接...");
    const payload = await requestJson("/api/connections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(connectionPayload()),
    });
    await loadConnections(payload.connection.id);
    applyConnection(payload.connection);
    await loadTargetTableOptions();
    closeConnectionDialog();
    setStatus("数据库连接已保存并选中。", "success");
  } catch (error) {
    setConnectionStatus(error.message, "error");
  }
}

async function removeSelectedConnection() {
  const id = connectionSelect.value;
  if (!id) {
    setStatus("请先选择要删除的连接。", "warn");
    return;
  }
  const current = savedConnections.find((item) => item.id === id);
  if (!window.confirm(`确认删除连接：${current?.name || id}？`)) return;
  await requestJson(`/api/connections?id=${encodeURIComponent(id)}`, { method: "DELETE" });
  await loadConnections("");
  setStatus("数据库连接已删除。", "success");
}

async function previewFile() {
  previewButton.disabled = true;
  try {
    setStatus("正在读取文件...");
    const payload = await requestJson("/api/preview", {
      method: "POST",
      body: buildFormData(false),
    });
    if (radioValue("targetMode") === "manual" && !tableName.value) {
      tableName.value = payload.suggestedTable;
    }
    currentColumns = payload.columns;
    renderMapping(payload.columns);
    renderTable(previewTable, payload.columns, payload.preview);
    const sheet = payload.selectedSheet ? ` · Sheet: ${payload.selectedSheet}` : "";
    previewMeta.textContent = `${payload.fileName}${sheet} · ${payload.totalRows} 行 · ${payload.columns.length} 列`;
    mappingDialog.classList.remove("hidden");
    setStatus("预览完成。", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    previewButton.disabled = false;
  }
}

function confirmDangerousActions() {
  const mode = radioValue("importMode");
  const hasSql = ["beforeAllSql", "afterEachSql", "afterAllSql", "afterQuerySql", "customSql"].some((id) => $(`#${id}`).value.trim());
  const actions = [];
  if (mode === "overwrite") actions.push("覆盖会清空目标表数据");
  if (mode === "rebuild") actions.push("重建会删除并重新创建目标表");
  if ($("#deleteAfterSuccess").checked) actions.push("导入成功后会删除上传的源文件副本");
  if (hasSql) actions.push("将执行你填写的 SQL");
  if (!actions.length) return true;
  return window.confirm(`${actions.join("；")}。确认继续？`);
}

async function importFiles(event) {
  event.preventDefault();
  importButton.disabled = true;
  previewButton.disabled = true;
  try {
    if (!confirmDangerousActions()) {
      setStatus("已取消导入。", "warn");
      return;
    }
    setStatus("正在导入，请稍候...");
    const payload = await requestJson("/api/import", {
      method: "POST",
      body: buildFormData(true),
    });
    const summary = payload.summary;
    const exportInfo = payload.exportPath ? ` 查询结果已导出：${payload.exportPath}` : "";
    setStatus(
      `成功 ${summary.successFiles}/${summary.totalFiles} 个文件，写入 ${summary.rowsWritten} 行，更新 ${summary.rowsUpdated} 行，跳过 ${summary.rowsSkipped} 行。${exportInfo}`,
      summary.failedFiles ? "warn" : "success",
    );
    await Promise.all([loadTables(), loadLogs()]);
    if (payload.tableName) {
      await loadTable(payload.tableName);
    }
  } catch (error) {
    setStatus(error.message, "error");
    await loadLogs();
  } finally {
    importButton.disabled = false;
    previewButton.disabled = false;
  }
}

function renderMapping(columns) {
  if (!columns.length) {
    mappingTable.className = "mapping-empty";
    mappingTable.textContent = "预览文件后可调整字段名、跳过字段、设置默认值和更新匹配键";
    return;
  }
  mappingTable.className = "mapping-table";
  const rows = columns
    .map(
      (column, index) => `
        <tr data-index="${index}">
          <td><input class="map-enabled" type="checkbox" checked /></td>
          <td><input class="map-key" type="checkbox" ${index === 0 ? "checked" : ""} /></td>
          <td>${escapeHtml(column)}</td>
          <td><input class="map-target" value="${escapeHtml(column)}" /></td>
          <td><input class="map-default" /></td>
        </tr>`,
    )
    .join("");
  mappingTable.innerHTML = `
    <table>
      <thead><tr><th>启用</th><th>匹配键</th><th>源字段</th><th>目标字段</th><th>默认值</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function readMapping() {
  const rows = mappingTable.querySelectorAll("tbody tr");
  if (!rows.length) {
    return currentColumns.map((column, index) => ({
      sourceIndex: index,
      source: column,
      target: column,
      enabled: true,
      defaultValue: "",
      matchKey: index === 0,
    }));
  }
  return [...rows].map((row) => ({
    sourceIndex: Number(row.dataset.index),
    source: currentColumns[Number(row.dataset.index)] || "",
    target: row.querySelector(".map-target").value,
    enabled: row.querySelector(".map-enabled").checked,
    defaultValue: row.querySelector(".map-default").value,
    matchKey: row.querySelector(".map-key").checked,
  }));
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadTables() {
  const payload = await requestJson("/api/tables");
  tableMeta.textContent = `${payload.tables.length} 张表`;
  tables.innerHTML = payload.tables.length ? "" : "暂无导入表";
  for (const name of payload.tables) {
    const button = document.createElement("button");
    button.textContent = name;
    button.title = name;
    button.addEventListener("click", () => loadTable(name));
    tables.append(button);
  }
}

async function loadTable(name) {
  try {
    const payload = await requestJson(`/api/table?name=${encodeURIComponent(name)}`);
    selectedTableMeta.textContent = `${payload.tableName}，共 ${payload.totalRows} 行`;
    renderTable(tablePreview, payload.columns, payload.rows);
  } catch (error) {
    selectedTableMeta.textContent = "读取失败";
    tablePreview.className = "table-wrap empty";
    tablePreview.textContent = error.message;
  }
}

async function loadLogs() {
  const payload = await requestJson("/api/logs");
  logs.innerHTML = payload.logs.length ? "" : "暂无日志";
  for (const item of payload.logs) {
    const node = document.createElement("div");
    node.className = `log-item ${item.status === "成功" ? "success" : "failed"}`;
    node.innerHTML = `
      <strong>${escapeHtml(item.table_name)}<span>${escapeHtml(item.status)}</span></strong>
      <div>${escapeHtml(item.file_name)}</div>
      <span>${escapeHtml(item.created_at)} · ${escapeHtml(item.mode)} · 读 ${item.rows_read} · 写 ${item.rows_written} · 更新 ${item.rows_updated} · 跳过 ${item.rows_skipped}</span>
      <div>${escapeHtml(item.message)}</div>`;
    logs.append(node);
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`[data-panel="${tab.dataset.tab}"]`).classList.add("active");
  });
});

document.querySelectorAll(".conn-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".conn-tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".conn-panel").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`[data-conn-panel="${tab.dataset.connTab}"]`).classList.add("active");
  });
});

fileInput.addEventListener("change", () => setFiles(fileInput.files));
dirInput.addEventListener("change", () => setFiles(dirInput.files));
tableName.addEventListener("input", useManualTargetTable);
tableName.addEventListener("change", useManualTargetTable);
previewButton.addEventListener("click", previewFile);
importForm.addEventListener("submit", importFiles);
ensureImportTaskButton();
openMapping.addEventListener("click", () => mappingDialog.classList.remove("hidden"));
closeMapping.addEventListener("click", () => mappingDialog.classList.add("hidden"));
mappingDialog.addEventListener("click", (event) => {
  if (event.target === mappingDialog) mappingDialog.classList.add("hidden");
});
newConnection?.addEventListener("click", () => openConnectionDialog());
refreshConnections?.addEventListener("click", () => loadConnections().catch((error) => setStatus(error.message, "error")));
deleteConnection?.addEventListener("click", () => removeSelectedConnection().catch((error) => setStatus(error.message, "error")));
connectionSelect.addEventListener("change", () => {
  const current = savedConnections.find((item) => item.id === connectionSelect.value);
  if (current) applyConnection(current);
  loadTargetTableOptions();
});
document.querySelectorAll('input[name="targetDbType"]').forEach((item) => {
  item.addEventListener("change", () => loadTargetTableOptions());
});
["dbHost", "dbPort", "dbName", "dbUser", "dbPassword", "dbCharset"].forEach((id) => {
  $("#" + id).addEventListener("change", () => loadTargetTableOptions());
});
closeConnection?.addEventListener("click", closeConnectionDialog);
cancelConnection?.addEventListener("click", closeConnectionDialog);
testConnection?.addEventListener("click", testCurrentConnection);
saveConnection?.addEventListener("click", saveCurrentConnection);
connectionDialog?.addEventListener("click", (event) => {
  if (event.target === connectionDialog) closeConnectionDialog();
});

ensureImportTaskPanel();
Promise.all([loadConnections(), loadTables(), loadLogs(), loadImportTaskJobs()]).catch((error) => setStatus(error.message, "error"));
