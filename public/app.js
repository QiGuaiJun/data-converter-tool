const fileInput = document.querySelector("#fileInput");
const dirInput = document.querySelector("#dirInput");
const linkSourceFile = document.querySelector("#linkSourceFile");
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
let openedImportTaskId = "";
let importEditorVisible = false;
let selectedTaskSourcePath = "";
let activeImportTaskConfig = {};
let previewColumnTypes = {};
let previewTypeWarnings = [];

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

// P2-15：自定义确认对话框（替代阻塞式 window.confirm，桌面壳/自动化环境下
// 原生 confirm 会导致按钮永久禁用；Promise 化后确认与取消路径都必然恢复状态）。
let confirmDialogResolve = null;

function showConfirmDialog(message, okText = "确认继续") {
  const dialog = document.querySelector("#confirmDialog");
  if (!dialog) return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => {
    confirmDialogResolve = resolve;
    document.querySelector("#confirmDialogMessage").textContent = message;
    document.querySelector("#confirmDialogOk").textContent = okText;
    dialog.classList.remove("hidden");
    document.querySelector("#confirmDialogOk").focus();
  });
}

function closeConfirmDialog(result) {
  const dialog = document.querySelector("#confirmDialog");
  if (!dialog || dialog.classList.contains("hidden")) return;
  dialog.classList.add("hidden");
  if (confirmDialogResolve) {
    confirmDialogResolve(result);
    confirmDialogResolve = null;
  }
}

function confirmDialogVisible() {
  const dialog = document.querySelector("#confirmDialog");
  return Boolean(dialog && !dialog.classList.contains("hidden"));
}

function setImportEditorVisible(visible) {
  importEditorVisible = Boolean(visible);
  const shell = document.querySelector(".import-shell");
  if (shell) shell.classList.toggle("task-overview-mode", !importEditorVisible);
  document.body.classList.toggle("import-task-overview", !importEditorVisible);
  document.body.classList.toggle("import-task-editor", importEditorVisible);
}

function clearImportEditor() {
  selectedFiles = [];
  selectedTaskSourcePath = "";
  currentColumns = [];
  activeImportTaskConfig = {};
  previewColumnTypes = {};
  previewTypeWarnings = [];
  importForm.reset();
  renderMapping([]);
  if (connectionSelect && savedConnections.length) connectionSelect.value = savedConnections[0].id;
  fileList.textContent = "尚未选择文件";
  previewMeta.textContent = "暂无预览";
  previewTable.className = "table-wrap empty";
  previewTable.textContent = "选择文件后可预览前 20 行";
  tableName.value = "";
  const nameInput = document.querySelector("#importTaskName");
  const pathInput = document.querySelector("#importTaskPath");
  if (nameInput) nameInput.value = "";
  if (pathInput) pathInput.value = "";
  setStatus("已新建导入任务，请配置文件、目标表和导入选项。");
}

async function setFiles(files) {
  selectedFiles = [...files].filter((file) => /\.(csv|txt|xlsx|xlsm|xls|json|xml|dbf)$/i.test(file.name));
  currentColumns = [];
  previewColumnTypes = {};
  previewTypeWarnings = [];
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
  const taskPath = document.querySelector("#importTaskPath");
  if (taskPath) {
    setStatus("正在上传并关联任务源文件...");
    const source = await uploadTaskSource(selectedFiles);
    selectedTaskSourcePath = source.sourcePath;
    taskPath.value = source.sourcePath;
    taskPath.placeholder = "已自动关联网页托管源文件";
    taskPath.classList.remove("invalid-path");
    taskPath.dispatchEvent(new Event("change", { bubbles: true }));
  }
  setStatus(`已选择 ${selectedFiles.length} 个文件，正在读取目标数据库表...`);
  const tables = await loadTargetTableOptions();
  tableName.title = tables.length ? `目标数据库共有 ${tables.length} 张表` : "目标数据库暂无数据表";
  setStatus(
    `已选择 ${selectedFiles.length} 个文件；已实时读取目标数据库 ${tables.length} 张表，请在目标表输入框中选择。`,
    "success",
  );
}

async function uploadTaskSource(files) {
  const data = new FormData();
  for (const file of files) data.append("file", file, file.webkitRelativePath || file.name);
  return requestJson("/api/task-source", { method: "POST", body: data });
}

async function uploadLinkedSource(file) {
  // 方案②：把选中的本机源文件复制到服务器固定输入目录 linked_sources（同名覆盖）。
  const data = new FormData();
  data.append("file", file, file.name);
  return requestJson("/api/import/choose-source", { method: "POST", body: data });
}

const LINK_SOURCE_DB = "dataToolLinkedSources";
const LINK_SOURCE_STORE = "files";
function linkDbOpen() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(LINK_SOURCE_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(LINK_SOURCE_STORE, { keyPath: "name" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function linkDbPut(record) {
  const db = await linkDbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(LINK_SOURCE_STORE, "readwrite");
    tx.objectStore(LINK_SOURCE_STORE).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
async function linkDbAll() {
  const db = await linkDbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(LINK_SOURCE_STORE, "readonly");
    const all = tx.objectStore(LINK_SOURCE_STORE).getAll();
    all.onsuccess = () => resolve(all.result || []);
    all.onerror = () => reject(all.error);
  });
}
async function rememberLinkedHandle(file, handle) {
  try {
    if (handle && typeof handle.requestPermission === "function") {
      try { await handle.requestPermission({ mode: "read" }); } catch {}
    }
    await linkDbPut({ name: file.name, handle, lastModified: file.lastModified });
  } catch (error) {
    console.warn("记住源文件授权失败（不影响本次关联）:", error);
  }
}
async function syncLinkedSources() {
  let records = [];
  try { records = await linkDbAll(); } catch { return; }
  for (const record of records) {
    const { name, handle } = record;
    if (!handle || typeof handle.getFile !== "function") continue;
    try {
      let permission = "granted";
      if (typeof handle.queryPermission === "function") permission = await handle.queryPermission({ mode: "read" });
      if (permission === "prompt" && typeof handle.requestPermission === "function") {
        try { permission = await handle.requestPermission({ mode: "read" }); } catch {}
      }
      if (permission !== "granted") continue;
      const file = await handle.getFile();
      if (file.lastModified === record.lastModified) continue;
      const source = await uploadLinkedSource(file);
      await linkDbPut({ name: file.name, handle, lastModified: file.lastModified });
      console.log(`[源文件自动同步] ${file.name} 已更新 -> ${source.sourcePath}`);
    } catch (error) {
      console.warn(`[源文件同步失败] ${name}:`, error && error.message ? error.message : error);
    }
  }
}
// 方案②：页面常驻时自动保持本机源文件为最新。首次关联后，本机文件在原路径/同名更新，
// 打开本页面即自动同步到服务器 linked_sources，定时任务读到的就是最新版（无需再手动指定）。
setTimeout(syncLinkedSources, 3000);
setInterval(syncLinkedSources, 60000);

async function chooseOriginalSourceFile() {
  // 浏览器原生选择源文件：把文件上传并复制到服务器固定输入目录 linked_sources，
  // 任务路径指向该固定位置，定时任务可稳定读取（本机文件更新后请重新关联以覆盖旧副本）。
  if (!window.showOpenFilePicker) {
    setStatus("您的浏览器不支持文件直选，请在任务路径框中手动填写本机文件的完整路径（服务器能直接读取），再保存为任务。", "info");
    return;
  }
  let handle;
  try {
    [handle] = await window.showOpenFilePicker({
      multiple: false,
      types: [
        {
          description: "数据文件",
          accept: {
            "text/csv": [".csv", ".txt"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx", ".xlsm"],
            "application/vnd.ms-excel": [".xls"],
            "application/json": [".json"],
            "application/xml": [".xml"],
            "application/dbf": [".dbf"],
          },
        },
      ],
    });
  } catch (error) {
    if (error && error.name === "AbortError") {
      setStatus("已取消选择源文件。", "info");
      return;
    }
    setStatus(`选择源文件失败：${error.message}`, "error");
    return;
  }
  const file = await handle.getFile();
  await rememberLinkedHandle(file, handle);
  setStatus("正在上传并关联任务源文件...");
  try {
    const source = await uploadLinkedSource(file);
    selectedFiles = [];
    selectedTaskSourcePath = source.sourcePath;
    const taskPath = document.querySelector("#importTaskPath");
    if (taskPath) {
      taskPath.value = source.sourcePath;
      taskPath.classList.remove("invalid-path");
      taskPath.dispatchEvent(new Event("change", { bubbles: true }));
    }
    restoreTaskSource(source.sourcePath);
    setStatus(`已关联本机原文件：${file.name}。⚠️ 请立即点击「保存为任务」重新保存，新路径才会生效（只点关联不保存，任务仍会用旧的一次性副本路径，定时执行会报错）。保存后只要路径和文件名不变，本机更新会自动同步到服务器，请保持本页面打开。`, "error");
  } catch (error) {
    setStatus(`上传源文件失败：${error.message}`, "error");
  }
}

function buildFormData(includeAllFiles = true) {
  const savedSourcePath = selectedTaskSourcePath || document.querySelector("#importTaskPath")?.value?.trim() || "";
  if (includeAllFiles && !selectedFiles.length && !savedSourcePath) {
    throw new Error("请先选择文件。");
  }
  const data = new FormData();
  if (selectedFiles.length) {
    for (const file of includeAllFiles ? selectedFiles : [selectedFiles[0]]) {
      data.append("file", file);
    }
  }
  if (!selectedFiles.length && savedSourcePath) {
    data.append("sourcePath", savedSourcePath);
  }

  data.append("tableName", radioValue("targetMode") === "manual" ? tableName.value : "");
  data.append("targetMode", radioValue("targetMode"));
  data.append("matchMode", radioValue("matchMode"));
  data.append("typeMode", radioValue("typeMode"));
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
  data.append("columnTypeOverrides", JSON.stringify(readColumnTypeOverrides()));
  return data;
}

function formDataToImportConfig(data) {
  const config = {};
  for (const [key, value] of data.entries()) {
    if (key !== "file") config[key] = String(value);
  }
  return config;
}

function selectedImportTaskDefaults(existingJob, existingConfig = {}) {
  return {
    name: existingJob?.name || tableName.value || selectedFiles[0]?.name?.replace(/\.[^.]+$/, "") || "导入任务",
    path: existingConfig.path || "",
  };
}

function isAbsoluteTaskPath(path) {
  return /^[A-Za-z]:[\\/]/.test(path) || /^\\\\[^\\]+[\\][^\\]+/.test(path) || path.startsWith("/");
}

function syncImportTaskEditor(job) {
  const step = job ? importTaskStep(job) : null;
  const defaults = selectedImportTaskDefaults(job, step?.config || {});
  const nameInput = document.querySelector("#importTaskName");
  const pathInput = document.querySelector("#importTaskPath");
  if (nameInput) nameInput.value = defaults.name;
  if (pathInput) {
    const validPath = defaults.path && isAbsoluteTaskPath(defaults.path);
    pathInput.value = validPath ? defaults.path : "";
    pathInput.placeholder = validPath
      ? "已关联本机源文件"
      : defaults.path
        ? `旧任务未保存完整路径，请重新选择：${defaults.path}`
        : "选择源文件后自动关联完整路径";
    pathInput.classList.toggle("invalid-path", Boolean(defaults.path) && !validPath);
  }
}

function importModeLabel(mode) {
  return {
    append: "追加",
    update: "更新",
    overwrite: "覆盖",
    rebuild: "重建",
  }[mode] || mode || "未选择";
}

async function saveImportTask() {
  const editingJobId = selectedImportTaskId || openedImportTaskId;
  const existingJob = importTaskJobs.find((item) => item.id === editingJobId);
  const existingStep = existingJob ? importTaskStep(existingJob) : null;
  const existingConfig = existingStep?.config || {};
  const data = buildFormData(false);
  const config = formDataToImportConfig(data);
  const selectedMode = radioValue("importMode") || existingConfig.importMode || "append";
  config.importMode = selectedMode;
  const taskName = (document.querySelector("#importTaskName")?.value || "").trim();
  const path = (document.querySelector("#importTaskPath")?.value || "").trim();
  if (!taskName) throw new Error("请填写任务名称。");
  if (!path) throw new Error("请在任务路径中填写后台定时执行时可访问的本机文件或目录路径。");
  if (!isAbsoluteTaskPath(path)) {
    throw new Error(`定时任务必须填写完整路径，不能只填写文件名：${path}。例如：D:\\data\\${path}`);
  }
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
  openedImportTaskId = result.job.id;
  await loadImportTaskJobs();
  const savedJob = importTaskJobs.find((item) => item.id === result.job.id);
  const savedMode = importTaskStep(savedJob || result.job).config?.importMode;
  if (savedMode !== selectedMode) {
    throw new Error(`保存校验失败：当前选择为${importModeLabel(selectedMode)}，但任务保存为${importModeLabel(savedMode)}。`);
  }
  openSelectedImportTask();
  clearImportDraft();
  setStatus(`${existingJob ? "已更新" : "已保存"}导入任务：${result.job.name}，导入模式：${importModeLabel(savedMode)}。`, "success");
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
  // 只认"单步导入任务"：导入任务 = 仅一个 import 步骤的资产。
  // 多步作业即使首步是 import（如全链路作业）也不应混入导入任务列表。
  return (job.steps || []).length === 1 && (job.steps || [])[0]?.type === "import";
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
      <div class="module-task-editor">
        <label>任务名称<input id="importTaskName" placeholder="例如 春节红包墙" /></label>
        <label>文件路径<input id="importTaskPath" placeholder="选择源文件后自动关联完整路径" /></label>
      </div>
      <div id="importTaskList" class="module-task-list empty">暂无导入任务</div>`;
    shell.insertAdjacentElement("afterbegin", panel);
    document.querySelector("#openImportTask").addEventListener("click", openSelectedImportTask);
    document.querySelector("#newImportTask").addEventListener("click", () => {
      const hasSelection = Boolean(selectedImportTaskId && importTaskJobs.some((job) => job.id === selectedImportTaskId));
      if (hasSelection) {
        saveImportTask().catch((error) => setStatus(error.message, "error"));
      } else {
        startNewImportTask();
      }
    });
    document.querySelector("#deleteImportTask").addEventListener("click", deleteSelectedImportTask);
  }
}

function startNewImportTask() {
  selectedImportTaskId = "";
  openedImportTaskId = "";
  updateImportTaskSelection();
  clearImportEditor();
  clearImportDraft();
  setImportEditorVisible(true);
}

function updateImportTaskSelection() {
  const hasSelection = Boolean(selectedImportTaskId && importTaskJobs.some((job) => job.id === selectedImportTaskId));
  document.querySelectorAll("#importTaskList [data-id]").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === selectedImportTaskId);
  });
  const openButton = document.querySelector("#openImportTask");
  const saveButton = document.querySelector("#newImportTask");
  const deleteButton = document.querySelector("#deleteImportTask");
  if (openButton) openButton.disabled = !hasSelection;
  if (saveButton) saveButton.textContent = hasSelection ? "保存修改" : "新增导入";
  if (deleteButton) deleteButton.disabled = !hasSelection;
  syncImportTaskEditor(importTaskJobs.find((job) => job.id === selectedImportTaskId));
}

function renderImportTaskJobs() {
  ensureImportTaskPanel();
  const list = document.querySelector("#importTaskList");
  if (!list) return;
  if (!importTaskJobs.length) {
    list.className = "module-task-list empty";
    list.textContent = "暂无导入任务";
  } else {
    list.className = "module-task-list";
    list.innerHTML = importTaskJobs
      .map((job) => `<button type="button" class="module-task-item task-import ${job.id === selectedImportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><span class="task-type-icon">IN</span><span class="task-item-name">${escapeHtml(job.name)}</span></button>`)
      .join("");
  }
  document.querySelectorAll("#importTaskList [data-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedImportTaskId = button.dataset.id;
      updateImportTaskSelection();
    });
    button.addEventListener("dblclick", () => {
      selectedImportTaskId = button.dataset.id;
      openedImportTaskId = button.dataset.id;
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
  if (openedImportTaskId && !importTaskJobs.some((job) => job.id === openedImportTaskId)) {
    openedImportTaskId = "";
  }
  renderImportTaskJobs();
}

function applyImportTaskConfig(config) {
  activeImportTaskConfig = { ...(config || {}) };
  for (const [key, value] of Object.entries(config || {})) {
    setControlValue(key, value);
  }
  const restoredConfig = { ...config };
  restoredConfig.targetMode ||= restoredConfig.tableName ? "manual" : "auto";
  restoredConfig.matchMode ||= restoredConfig.mapping && restoredConfig.mapping !== "[]" ? "custom" : "auto";
  restoredConfig.typeMode ||= "auto";
  for (const name of ["targetMode", "matchMode", "typeMode", "importMode", "tableCase", "fieldCase", "targetDbType", "extraColumnMode", "writeMode", "commitMode"]) {
    if (restoredConfig?.[name]) setRadioValue(name, restoredConfig[name]);
  }
  if (connectionSelect) {
    const matchedConnection = findConnectionForConfig(config || {});
    if (matchedConnection) {
      connectionSelect.value = matchedConnection.id;
      applyConnection(matchedConnection);
    } else if (config?.connectionId && savedConnections.some((item) => item.id === config.connectionId)) {
      connectionSelect.value = config.connectionId;
    }
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
      if (config?.columnTypeOverrides) {
        try {
          const overrides = JSON.parse(config.columnTypeOverrides);
          [...mappingTable.querySelectorAll("tbody tr")].forEach((row) => {
            const select = row.querySelector(".map-type");
            const targetName = row.querySelector(".map-target")?.value || "";
            if (select && overrides[targetName]) select.value = overrides[targetName];
          });
        } catch (_) {
          // 忽略损坏的覆盖配置，保持“自动”推断。
        }
      }
    } catch (_) {
      renderMapping([]);
    }
  }
  loadTargetTableOptions(config?.tableName || "").catch((error) => setStatus(error.message, "error"));
}

function restoreTaskSource(path) {
  selectedFiles = [];
  selectedTaskSourcePath = String(path || "");
  if (!selectedTaskSourcePath) {
    fileList.textContent = "尚未选择文件";
    return;
  }
  const name = selectedTaskSourcePath.split(/[\\/]/).filter(Boolean).at(-1) || selectedTaskSourcePath;
  fileList.innerHTML = `<div class="file-item persisted-file"><span>1</span><div><strong>${escapeHtml(name)}</strong><small>${escapeHtml(selectedTaskSourcePath)}</small></div></div>`;
  previewMeta.textContent = `已关联任务源文件：${name}`;
}

function openSelectedImportTask() {
  const job = importTaskJobs.find((item) => item.id === selectedImportTaskId);
  if (!job) return;
  openedImportTaskId = job.id;
  setImportEditorVisible(true);
  const step = importTaskStep(job);
  applyImportTaskConfig(step.config || {});
  restoreTaskSource(step.config?.path || "");
  syncImportTaskEditor(job);
  setStatus(`已打开导入任务：${job.name}${step.config?.path ? `，定时执行路径：${step.config.path}` : ""}`, "success");
}

async function deleteSelectedImportTask() {
  const job = importTaskJobs.find((item) => item.id === selectedImportTaskId);
  if (!job) return;
  if (!window.confirm(`确定删除导入任务“${job.name}”吗？关联的定时任务也会一起删除。`)) return;
  await requestJson(`/api/jobs?id=${encodeURIComponent(job.id)}`, { method: "DELETE" });
  selectedImportTaskId = "";
  openedImportTaskId = "";
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

function postJson(url, body) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  $("#connHost").value = connection.host || $("#dbHost").value || "";
  $("#connPort").value = connection.port || $("#dbPort").value || "3306";
  $("#connUser").value = connection.user || $("#dbUser").value || "root";
  $("#connPassword").value = connection.id ? "" : connection.password || $("#dbPassword").value || "";
  const database = connection.database || $("#dbName").value || "";
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

function sameText(left, right) {
  return String(left || "").trim().toLowerCase() === String(right || "").trim().toLowerCase();
}

function findConnectionForConfig(config = {}) {
  const wantedId = String(config.connectionId || "").trim();
  if (wantedId) {
    const byId = savedConnections.find((item) => item.id === wantedId);
    if (byId) return byId;
  }
  return savedConnections.find(
    (item) =>
      sameText(item.host, config.dbHost) &&
      sameText(item.port || "3306", config.dbPort || "3306") &&
      sameText(item.database, config.dbName) &&
      sameText(item.user, config.dbUser),
  );
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
  const selectedConnectionId = connectionSelect?.value || "";
  params.set("connectionId", selectedConnectionId);
  // 密码不再放入连接参数：有 connectionId 时后端从已保存连接中补齐 host/密码等，
  // 前端只需 connectionId + targetDbType；仅"使用默认连接参数"手填直连（无
  // connectionId）时才携带非密码连接字段，随 POST body 传输。
  if (!selectedConnectionId) {
    for (const id of ["dbHost", "dbPort", "dbName", "dbUser", "dbCharset"]) {
      params.set(id, $(`#${id}`).value);
    }
  }
  if (!selectedConnectionId && activeImportTaskConfig?.dbPasswordSecret && !$("#dbPassword").value) {
    params.set("dbPasswordSecret", activeImportTaskConfig.dbPasswordSecret);
  }
  if (activeImportTaskConfig?.sslEnabled) params.set("sslEnabled", activeImportTaskConfig.sslEnabled);
  for (const key of ["sslCa", "sslCert", "sslKey"]) {
    if (activeImportTaskConfig?.[key]) params.set(key, activeImportTaskConfig[key]);
  }
  return params;
}

async function loadTargetTableOptions(preferredValue = tableName.value) {
  const keepValue = preferredValue || tableName.value || "";
  tableName.disabled = true;
  try {
    const payload = await postJson("/api/target-tables", Object.fromEntries(connectionParams()));
    const tableItems = payload.tables || [];
    if (targetTableOptions) targetTableOptions.innerHTML = "";
    for (const item of tableItems) {
      const option = document.createElement("option");
      option.value = item;
      if (targetTableOptions) targetTableOptions.append(option);
    }
    if (keepValue) {
      tableName.value = keepValue;
      if (!tableItems.includes(keepValue) && targetTableOptions) {
        const option = document.createElement("option");
        option.value = keepValue;
        targetTableOptions.append(option);
      }
    }
    tableName.placeholder = tableItems.length ? "请选择目标数据库中的表，或直接输入表名" : "目标库暂无表，可直接输入新表名";
    tableName.title = tableItems.length ? `已读取 ${tableItems.length} 张目标表` : "目标库暂无表，可直接输入新表名";
    return tableItems;
  } catch (error) {
    if (targetTableOptions) targetTableOptions.innerHTML = "";
    if (keepValue) tableName.value = keepValue;
    tableName.placeholder = "目标表读取失败，可直接输入表名";
    tableName.title = `目标表读取失败：${error.message}`;
    throw error;
  } finally {
    tableName.disabled = false;
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
    } else if (payload.databases.length) {
      $("#connDatabase").value = payload.databases[0];
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
    previewColumnTypes = payload.columnTypes || {};
    previewTypeWarnings = payload.typeWarnings || [];
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

async function confirmDangerousActions() {
  const mode = radioValue("importMode");
  const hasSql = ["beforeAllSql", "afterEachSql", "afterAllSql", "afterQuerySql", "customSql"].some((id) => $(`#${id}`).value.trim());
  const actions = [];
  if (mode === "overwrite") actions.push("覆盖会清空目标表数据");
  if (mode === "rebuild") actions.push("重建会删除并重新创建目标表");
  if ($("#deleteAfterSuccess").checked) actions.push("导入成功后会删除上传的源文件副本");
  if (hasSql) actions.push("将执行你填写的 SQL");
  if (!actions.length) return true;
  return showConfirmDialog(`${actions.join("；")}。确认继续？`);
}

let importInFlight = false;

async function importFiles(event) {
  event.preventDefault();
  if (importInFlight || confirmDialogVisible()) return;
  if (!(await confirmDangerousActions())) {
    setStatus("已取消导入。", "warn");
    return;
  }
  importInFlight = true;
  importButton.disabled = true;
  previewButton.disabled = true;
  try {
    setStatus("正在导入，请稍候...");
    const payload = await requestJson("/api/import", {
      method: "POST",
      body: buildFormData(true),
    });
    const summary = payload.summary;
    const exportInfo = payload.exportPath ? ` 查询结果已导出：${payload.exportPath}` : "";
    const skipInfo = summary.skippedFiles ? `，跳过未变更文件 ${summary.skippedFiles} 个` : "";
    setStatus(
      `成功 ${summary.successFiles}/${summary.totalFiles} 个文件，写入 ${summary.rowsWritten} 行，更新 ${summary.rowsUpdated} 行，跳过 ${summary.rowsSkipped} 行${skipInfo}。${exportInfo}`,
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
    importInFlight = false;
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
  const warningByColumn = {};
  for (const warning of previewTypeWarnings || []) {
    if (warning && warning.column) warningByColumn[warning.column] = warning.reason || "";
  }
  const rows = columns
    .map((column, index) => {
      const inferred = previewColumnTypes[column] || "text";
      const warningReason = warningByColumn[column] || "";
      const warningIcon = warningReason
        ? `<span class="type-warning" title="${escapeHtml(warningReason)}">⚠️</span>`
        : "";
      return `
        <tr data-index="${index}"${warningReason ? ' class="type-warning-row"' : ""}>
          <td><input class="map-enabled" type="checkbox" checked /></td>
          <td><input class="map-key" type="checkbox" ${index === 0 ? "checked" : ""} /></td>
          <td>${escapeHtml(column)}</td>
          <td><input class="map-target" value="${escapeHtml(column)}" /></td>
          <td>${warningIcon}<select class="map-type">${typeSelectOptions(inferred)}</select></td>
          <td><input class="map-default" /></td>
        </tr>`;
    })
    .join("");
  mappingTable.innerHTML = `
    <table>
      <thead><tr><th>启用</th><th>匹配键</th><th>源字段</th><th>目标字段</th><th>目标类型</th><th>默认值</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function typeSelectOptions(inferred) {
  const options = [
    ["", `自动（${escapeHtml(inferred)}）`],
    ["bigint", "bigint"],
    ["double", "double"],
    ["date", "date"],
    ["datetime", "datetime"],
    ["text", "text"],
  ];
  return options.map(([value, label]) => `<option value="${escapeHtml(value)}">${label}</option>`).join("");
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

function readColumnTypeOverrides() {
  const overrides = {};
  const rows = mappingTable.querySelectorAll("tbody tr");
  for (const row of rows) {
    const select = row.querySelector(".map-type");
    const type = select ? select.value : "";
    if (!type) continue;
    const targetInput = row.querySelector(".map-target");
    const targetName = targetInput ? targetInput.value.trim() : "";
    if (!targetName) continue;
    overrides[targetName] = type;
  }
  return overrides;
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
  const payload = await postJson("/api/tables", Object.fromEntries(connectionParams()));
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
    const body = Object.fromEntries(connectionParams());
    body.name = name;
    const payload = await postJson("/api/table", body);
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

fileInput.addEventListener("change", () => setFiles(fileInput.files).catch((error) => setStatus(`文件关联失败：${error.message}`, "error")));
dirInput.addEventListener("change", () => setFiles(dirInput.files).catch((error) => setStatus(`文件关联失败：${error.message}`, "error")));
linkSourceFile?.addEventListener("click", () => chooseOriginalSourceFile().catch((error) => setStatus(`关联原文件失败：${error.message}`, "error")));
tableName.addEventListener("input", useManualTargetTable);
tableName.addEventListener("change", useManualTargetTable);
document.querySelector('input[name="targetMode"][value="manual"]').addEventListener("click", () => {
  loadTargetTableOptions()
    .then((items) => {
      setStatus(`已读取目标数据库 ${items.length} 张表，请选择目标表。`, "success");
      tableName.focus();
      try {
        if (typeof tableName.showPicker === "function") tableName.showPicker();
      } catch (_) {
        // 部分浏览器不允许异步展开，选项仍已加载，用户再次点击即可查看。
      }
    })
    .catch((error) => setStatus(`目标表读取失败：${error.message}`, "error"));
});
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
  loadTables().catch((error) => setStatus(`已导入表读取失败：${error.message}`, "error"));
});
document.querySelectorAll('input[name="targetDbType"]').forEach((item) => {
  item.addEventListener("change", () => {
    loadTargetTableOptions();
    loadTables().catch((error) => setStatus(`已导入表读取失败：${error.message}`, "error"));
  });
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
setImportEditorVisible(false);

// P2-16：导入编辑器草稿。配置实时存 localStorage，刷新后自动恢复；
// 保存为任务或新建任务后清除。密码类字段（数据库密码、Excel 密码）不落盘。
const IMPORT_DRAFT_KEY = "dc_import_draft_v1";

function collectImportDraft() {
  const scope = document.querySelector("main.import-shell");
  if (!scope) return null;
  const draft = { values: {}, checks: {}, radios: {} };
  scope.querySelectorAll("input, select, textarea").forEach((control) => {
    if (control.type === "file" || control.type === "password") return;
    if (control.type === "radio") {
      if (control.checked) draft.radios[control.name] = control.value;
      return;
    }
    if (control.type === "checkbox") {
      if (control.id) draft.checks[control.id] = control.checked;
      return;
    }
    if (control.id) draft.values[control.id] = control.value;
  });
  return draft;
}

let importDraftTimer = 0;
// 用户编辑过后才允许 pagehide 兜底保存；保存/新建任务主动清草稿后不再回写
let importDraftDirty = false;

function scheduleImportDraftSave() {
  importDraftDirty = true;
  clearTimeout(importDraftTimer);
  importDraftTimer = setTimeout(() => {
    const draft = collectImportDraft();
    if (draft) {
      try {
        localStorage.setItem(IMPORT_DRAFT_KEY, JSON.stringify(draft));
      } catch (_) {
        // 存储满或被禁用时静默放弃，不影响正常导入流程。
      }
    }
  }, 400);
}

function clearImportDraft() {
  importDraftDirty = false;
  try {
    localStorage.removeItem(IMPORT_DRAFT_KEY);
  } catch (_) {
    // 忽略
  }
}

function restoreImportDraft() {
  let raw = "";
  try {
    raw = localStorage.getItem(IMPORT_DRAFT_KEY) || "";
  } catch (_) {
    return false;
  }
  if (!raw) return false;
  let draft;
  try {
    draft = JSON.parse(raw);
  } catch (_) {
    clearImportDraft();
    return false;
  }
  if (!draft || typeof draft !== "object") return false;
  const scope = document.querySelector("main.import-shell");
  if (!scope) return false;
  let restored = 0;
  Object.entries(draft.values || {}).forEach(([id, value]) => {
    const control = scope.querySelector(`#${CSS.escape(id)}`);
    if (control && control.value !== value) {
      control.value = value;
      restored += 1;
    }
  });
  Object.entries(draft.checks || {}).forEach(([id, checked]) => {
    const control = scope.querySelector(`#${CSS.escape(id)}`);
    if (control && control.checked !== Boolean(checked)) {
      control.checked = Boolean(checked);
      restored += 1;
    }
  });
  Object.entries(draft.radios || {}).forEach(([name, value]) => {
    setRadioValue(name, value);
  });
  return restored > 0 || Object.keys(draft.radios || {}).length > 0;
}

const importShell = document.querySelector("main.import-shell");
importShell?.addEventListener("input", scheduleImportDraftSave);
importShell?.addEventListener("change", scheduleImportDraftSave);
// 刷新/关闭前的兜底：防抖未到点时立即落盘，避免"输入后马上刷新"丢草稿
window.addEventListener("pagehide", () => {
  if (!importDraftDirty) return;
  const draft = collectImportDraft();
  if (draft) {
    try {
      localStorage.setItem(IMPORT_DRAFT_KEY, JSON.stringify(draft));
    } catch (_) {
      // 忽略
    }
  }
});

document.querySelector("#confirmDialogOk")?.addEventListener("click", () => closeConfirmDialog(true));
document.querySelector("#confirmDialogCancel")?.addEventListener("click", () => closeConfirmDialog(false));
document.querySelector("#confirmDialogClose")?.addEventListener("click", () => closeConfirmDialog(false));
document.querySelector("#confirmDialog")?.addEventListener("click", (event) => {
  if (event.target === document.querySelector("#confirmDialog")) closeConfirmDialog(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeConfirmDialog(false);
});

async function initImportPage() {
  // P2-16：草稿恢复必须独立于表/日志/任务加载结果。此前 Promise.all 里任一请求
  // 失败（如 loadTables 与 loadConnections 并发竞态时空参数直连被拒）都会整体
  // reject，短路草稿恢复。现在：连接列表先加载 → 恢复草稿（其连接选择参与
  // 后续表加载）→ 其余数据 allSettled 降级加载，失败仅在无草稿时提示。
  let connectionError = null;
  try {
    await loadConnections();
  } catch (error) {
    connectionError = error;
  }
  let draftRestored = false;
  try {
    draftRestored = restoreImportDraft();
  } catch (_) {
    draftRestored = false;
  }
  if (draftRestored) {
    setImportEditorVisible(true);
    setStatus("已恢复上次未保存的导入配置草稿（文件需重新选择；保存任务后草稿自动清除）。", "warn");
  } else if (connectionError) {
    setStatus(connectionError.message, "error");
  }
  const settled = await Promise.allSettled([loadTables(), loadLogs(), loadImportTaskJobs()]);
  if (!draftRestored) {
    const firstError = settled.find((item) => item.status === "rejected");
    if (firstError) {
      setStatus((firstError.reason && firstError.reason.message) || String(firstError.reason), "error");
    }
  }
}

initImportPage();
