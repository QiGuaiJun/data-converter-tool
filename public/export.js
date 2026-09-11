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
// P2-32：未保存草稿的内存副本。打开模块时停留在任务列表，
// 点「新增导出」时再复用，避免被 resetExportEditor 清掉。
let pendingExportDraft = null;
// 浏览器不支持 File System Access API（showDirectoryPicker/showSaveFilePicker）时切换为“仅下载”模式
let exportFallbackToDownload = false;
// 用户用浏览器选择文件夹/文件后，输入框只显示 handle.name（沙箱不暴露绝对路径）。
// 服务器端仍需要一个可写的相对/绝对路径，因此把选择前的服务器路径暂存到这里，collectPayload 用它，
// 避免把“已选择文件夹：xxx”这类展示文本当作服务器目标路径发送。
let exportFolderServerValue = "";
let outputNameServerValue = "";

// P2-33：导出目标的持久化。
// 浏览器的 File System Access API 在安全沙箱里只给 handle.name（不暴露绝对路径），
// 且 FileSystemDirectoryHandle/FileSystemFileHandle 无法序列化进服务端 JSON 配置，
// 所以「...」直选的结果以前保存后就丢了。这里把句柄按任务 id 存进 IndexedDB（结构化克隆），
// 重开任务时恢复；手填的绝对路径则照旧进任务配置、由服务端直接落盘（定时任务也生效）。
const EXPORT_TARGET_DB = "dataToolExportTargets";
const EXPORT_TARGET_STORE = "targets";
const EXPORT_TARGET_DRAFT_KEY = "__draft__";

function exportTargetDbOpen() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(EXPORT_TARGET_DB, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(EXPORT_TARGET_STORE)) {
        db.createObjectStore(EXPORT_TARGET_STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function exportTargetDbPut(record) {
  const db = await exportTargetDbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(EXPORT_TARGET_STORE, "readwrite");
    tx.objectStore(EXPORT_TARGET_STORE).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function exportTargetDbGet(key) {
  const db = await exportTargetDbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(EXPORT_TARGET_STORE, "readonly");
    const request = tx.objectStore(EXPORT_TARGET_STORE).get(key);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
}

async function exportTargetDbDelete(key) {
  const db = await exportTargetDbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(EXPORT_TARGET_STORE, "readwrite");
    tx.objectStore(EXPORT_TARGET_STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// 编辑中的任务用任务 id 作键；尚未保存的新任务先用 __draft__，保存成功后迁移到新 id。
function exportTargetKey() {
  return selectedExportTaskId || EXPORT_TARGET_DRAFT_KEY;
}

async function rememberExportTarget() {
  try {
    const key = exportTargetKey();
    if (!exportDirectoryHandle && !exportFileHandle) {
      await exportTargetDbDelete(key);
      return;
    }
    await exportTargetDbPut({
      key,
      folderHandle: exportDirectoryHandle || null,
      folderName: exportDirectoryHandle ? exportDirectoryHandle.name : "",
      fileHandle: exportFileHandle || null,
      fileName: exportFileHandle ? exportFileHandle.name : "",
      updatedAt: Date.now(),
    });
  } catch (error) {
    console.warn("记住导出目标失败（不影响本次选择）:", error);
  }
}

async function migrateExportTargetRecord(jobId) {
  if (!jobId) return;
  try {
    const draft = await exportTargetDbGet(EXPORT_TARGET_DRAFT_KEY);
    if (!draft) return;
    await exportTargetDbPut({ ...draft, key: jobId });
    await exportTargetDbDelete(EXPORT_TARGET_DRAFT_KEY);
  } catch (error) {
    console.warn("迁移导出目标记录失败:", error);
  }
}

async function forgetExportTarget(jobId) {
  if (!jobId) return;
  try {
    await exportTargetDbDelete(jobId);
  } catch (error) {
    console.warn("清理导出目标记录失败:", error);
  }
}

// queryPermission 不会弹窗，可在页面加载时安全调用；requestPermission 必须在用户手势里调用。
async function ensureHandlePermission(handle, mode, options = {}) {
  if (!handle || typeof handle.queryPermission !== "function") return "granted";
  let permission = "denied";
  try {
    permission = await handle.queryPermission({ mode });
  } catch (_) {
    return "granted";
  }
  if (permission === "granted") return permission;
  if (!options.request) return permission;
  try {
    return await handle.requestPermission({ mode });
  } catch (_) {
    return "denied";
  }
}

function isAbsolutePath(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  return /^[a-zA-Z]:[\\/]/.test(text) || text.startsWith("\\\\") || text.startsWith("/");
}

// 「...」直选后输入框里是展示文本（已选择文件夹：xxx），不能当服务端路径发送；
// 历史上默认值写死为相对路径 "exports"，服务端会直接拒绝，这里一并按空处理（回落到服务器默认导出目录）。
function normalizeServerPath(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (text.startsWith("已选择文件夹：") || text.startsWith("已选择文件：")) return "";
  if (text === "exports") return "";
  return text;
}

// 目标路径自检：返回空串表示没问题，否则返回给用户看的错误文案。
function exportTargetPathProblem() {
  const mode = radioValue("exportTargetMode");
  const raw = mode === "file" ? $("#outputName").value : $("#exportFolder").value;
  const text = String(raw || "").trim();
  if (!text) return "";
  if (text.startsWith("已选择文件夹：") || text.startsWith("已选择文件：")) return "";
  if (text === "exports") return "";
  if (!isAbsolutePath(text)) {
    return `目标路径“${text}”不是完整路径。请填写绝对路径（例如 D:\\导出），或点右侧「...」直接选择文件夹；目标框留空则导出到服务器默认目录。`;
  }
  return "";
}

// 导出前确认句柄仍可用（首次导出会在这里请求一次写权限，必须在用户点击的手势内）。
async function ensureExportTargetReady() {
  if (exportFallbackToDownload) return { ok: true };
  const mode = radioValue("exportTargetMode");
  const handle = mode === "file" ? exportFileHandle : exportDirectoryHandle;
  const name = handle ? handle.name : "";
  if (!handle) {
    // 没有浏览器句柄时，目标位置由服务端 export_target_path() 按填写的绝对路径直接落盘。
    // 这里要区分「填了绝对路径」和「什么都没填」：前者已经写到了用户要的位置，
    // 不能再提示"已生成在服务器默认导出目录"（会让用户以为设置没生效）。
    const serverPath = normalizeServerPath(mode === "file" ? $("#outputName").value : $("#exportFolder").value);
    return serverPath
      ? { ok: false, reason: "server", serverPath }
      : { ok: false, reason: "missing" };
  }
  const permission = await ensureHandlePermission(handle, "readwrite", { request: true });
  if (permission !== "granted") {
    return { ok: false, reason: "denied", name };
  }
  return { ok: true, name };
}

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
  // 视图切换会影响工具条第三个按钮的文案（新增导出 / 保存修改 / 保存为新任务），
  // 统一在这里刷新，避免各处调用点漏掉导致标签滞后。
  updateExportTaskSelection();
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
  exportFallbackToDownload = false;
  exportFolderServerValue = "";
  outputNameServerValue = "";
}

function startNewExportTask() {
  selectedExportTaskId = "";
  updateExportTaskSelection();
  if (pendingExportDraft) {
    // P2-32：已有未保存草稿，直接沿用当前表单内容（含导出对象勾选），不重置。
    pendingExportDraft = null;
  } else {
    resetExportEditor();
    clearExportDraft();
  }
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
  saveExportDraftNow();
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

function postJson(url, body) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
    // 只显示连接名称，不在界面上暴露主机 / 库名等连接信息。
    option.textContent = item.name || item.id;
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
  const payload = await postJson("/api/export/sources", connectionPayload());
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
          <small>${escapeHtml(item.type || "")}${item.rows ? ` · ${item.rowsApproximate ? "约 " : ""}${item.rows}` : ""}</small>
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
    // P2-33：以前漏发 exportTargetMode，后端 export_target_path() 恒按 folder 分支处理，
    // 导致「导出到指定文件」在手填绝对路径时完全无效。
    exportTargetMode: radioValue("exportTargetMode") || "folder",
    exportFolder: normalizeServerPath($("#exportFolder").value),
    exportFileName: $("#exportFileName").value.trim(),
    outputName: normalizeServerPath($("#outputName").value),
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

// 根据所选扩展名构造 showSaveFilePicker 的 accept 映射
function exportFileTypesForExtension(extension) {
  const acceptByExtension = {
    xlsx: { "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"] },
    xlsm: { "application/vnd.ms-excel.sheet.macroEnabled.12": [".xlsm"] },
    xls: { "application/vnd.ms-excel": [".xls"] },
    csv: { "text/csv": [".csv"] },
    txt: { "text/plain": [".txt"] },
    json: { "application/json": [".json"] },
    xml: { "application/xml": [".xml"] },
    dbf: { "application/dbf": [".dbf"] },
  };
  return acceptByExtension[extension] || { "application/octet-stream": [`.${extension}`] };
}

// 选择导出目标：优先用服务端原生对话框。
// 浏览器的 showDirectoryPicker/showSaveFilePicker 出于安全永远不给绝对路径（只给 handle.name），
// 所以界面上只能显示"已选择文件夹：中秋试饮"、存进任务配置时也只能是空串；
// 而服务端与浏览器同机运行，由服务端弹 Windows 原生对话框就能拿到 D:\导出 这样的完整路径，
// 该路径可以直接存进任务、由服务端落盘，定时任务（无浏览器）同样生效。
// 服务端不可用（非 Windows / 无桌面会话 / 旧版本）时，回退到浏览器直选。
async function pickTargetOnServer(mode) {
  const isFolder = mode === "folder";
  // 原生对话框是模态的，这个请求会一直挂到用户关闭窗口为止；期间禁用「...」按钮，
  // 否则连点几次就会在前一个对话框关掉后接连弹出好几个。
  const trigger = $(isFolder ? "#chooseExportFolder" : "#chooseExportFile");
  const restore = () => {
    if (trigger) trigger.disabled = false;
  };
  if (trigger) trigger.disabled = true;
  setStatus(isFolder ? "已打开系统文件夹选择框，请在弹出的窗口中选择…" : "已打开系统文件保存框，请选择保存位置…", "info");
  try {
    let response;
    try {
      response = await fetch("/api/export/choose-target", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          extension: ($("#exportExtension")?.value || "xlsx").replace(/^\./, ""),
          initial: normalizeServerPath(isFolder ? $("#exportFolder").value : $("#outputName").value),
          suggest: ($("#exportFileName")?.value || "").trim(),
        }),
      });
    } catch (_) {
      return { status: "unsupported", path: "" };
    }
    if (response.status === 404 || response.status === 501) {
      return { status: "unsupported", path: "" };
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      return { status: "unsupported", path: "" };
    }
    if (!payload || payload.ok !== true) {
      if (payload && payload.unsupported) return { status: "unsupported", path: "" };
      throw new Error((payload && payload.error) || "系统选择框返回异常。");
    }
    return { status: payload.path ? "picked" : "cancelled", path: payload.path || "" };
  } finally {
    restore();
  }
}

// 服务端已返回绝对路径：直接写进目标框（并停用浏览器句柄，避免两套机制互相覆盖）。
function applyServerPickedTarget(mode, path) {
  exportDirectoryHandle = null;
  exportFileHandle = null;
  exportFallbackToDownload = false;
  if (mode === "file") {
    outputNameServerValue = "";
    $("#outputName").value = path;
    $("#outputName").placeholder = "留空=按文件名生成，或填绝对路径 D:\\导出\\报表.xlsx";
    document.querySelector('input[name="exportTargetMode"][value="file"]').checked = true;
    forgetExportTarget(exportTargetKey()).catch(() => {});
    setStatus(`已选择目标文件：${path}（服务端直接写入，定时任务同样生效）`, "success");
    return;
  }
  exportFolderServerValue = "";
  $("#exportFolder").value = path;
  $("#exportFolder").placeholder = "留空=服务器默认目录，或填绝对路径 D:\\导出";
  document.querySelector('input[name="exportTargetMode"][value="folder"]').checked = true;
  // 清掉该任务旧的浏览器句柄记录，否则下次打开会被"已选择文件夹：xxx"覆盖掉这条绝对路径。
  forgetExportTarget(exportTargetKey()).catch(() => {});
  setStatus(`已选择导出文件夹：${path}（服务端直接写入该目录，定时任务同样生效）`, "success");
}

async function chooseFolder() {
  const picked = await pickTargetOnServer("folder");
  if (picked.status === "picked") {
    applyServerPickedTarget("folder", picked.path);
    return;
  }
  if (picked.status === "cancelled") {
    setStatus("已取消选择。", "info");
    return;
  }
  await chooseFolderInBrowser();
}

// 回退路径：浏览器直选。文件夹选择只在浏览器本地生效（导出后由 copyExportedFiles 复制过去）。
async function chooseFolderInBrowser() {
  if (!window.showDirectoryPicker) {
    exportFallbackToDownload = true;
    $("#exportFolder").placeholder = "您的浏览器不支持文件夹直选，导出后将通过浏览器下载文件";
    setStatus("无法打开系统文件夹选择框，且当前浏览器不支持文件夹直选，将改为下载文件（点击导出后用浏览器下载）。", "info");
    return;
  }
  let handle;
  try {
    handle = await window.showDirectoryPicker({ id: "export-folder", mode: "readwrite" });
  } catch (error) {
    if (error && error.name === "AbortError") {
      setStatus("已取消选择。", "info");
      return;
    }
    setStatus(`选择文件夹失败：${error.message}`, "error");
    return;
  }
  exportDirectoryHandle = handle;
  exportFileHandle = null;
  exportFallbackToDownload = false;
  // 浏览器安全沙箱不暴露绝对路径，只显示 handle.name；服务器端路径暂存起来供 collectPayload 使用
  if (!exportFolderServerValue) exportFolderServerValue = $("#exportFolder").value.trim() || "exports";
  $("#exportFolder").value = `已选择文件夹：${handle.name}`;
  $("#exportFolder").placeholder = "导出后复制到所选文件夹";
  document.querySelector('input[name="exportTargetMode"][value="folder"]').checked = true;
  // P2-33：句柄存进 IndexedDB，重开任务时自动恢复（以前保存后即丢失）
  await rememberExportTarget();
  setStatus(`已选择导出文件夹：${handle.name}（部分浏览器不提供绝对路径，仅浏览器导出时生效）`, "warn");
}

async function chooseFile() {
  const picked = await pickTargetOnServer("file");
  if (picked.status === "picked") {
    applyServerPickedTarget("file", picked.path);
    return;
  }
  if (picked.status === "cancelled") {
    setStatus("已取消选择。", "info");
    return;
  }
  await chooseFileInBrowser();
}

// 回退路径：浏览器原生保存对话框。
async function chooseFileInBrowser() {
  if (!window.showSaveFilePicker) {
    exportFallbackToDownload = true;
    $("#outputName").placeholder = "您的浏览器不支持文件直选，导出后将通过浏览器下载文件";
    setStatus("无法打开系统保存框，且当前浏览器不支持文件直选，将改为下载文件（点击导出后用浏览器下载）。", "info");
    return;
  }
  const extension = ($("#exportExtension").value || "xlsx").replace(/^\./, "");
  let rawName = ($("#outputName").value || "export").replace(/[\\/]/g, "/").split("/").pop() || "export";
  rawName = rawName.replace(/^已选择文件：/, "");
  const suggestedName = rawName.replace(/\.[^.]+$/, "") + `.${extension}`;
  let handle;
  try {
    handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description: "导出文件", accept: exportFileTypesForExtension(extension) }],
    });
  } catch (error) {
    if (error && error.name === "AbortError") {
      setStatus("已取消选择。", "info");
      return;
    }
    setStatus(`选择文件失败：${error.message}`, "error");
    return;
  }
  exportFileHandle = handle;
  exportDirectoryHandle = null;
  exportFallbackToDownload = false;
  if (!outputNameServerValue) outputNameServerValue = $("#outputName").value.trim() || "";
  $("#outputName").value = `已选择文件：${handle.name}`;
  $("#outputName").placeholder = "导出后写入该文件";
  document.querySelector('input[name="exportTargetMode"][value="file"]').checked = true;
  // P2-33：同文件夹——句柄存进 IndexedDB，重开任务时自动恢复
  await rememberExportTarget();
  setStatus(`已选择目标文件：${handle.name}（部分浏览器不提供绝对路径，仅浏览器导出时生效）`, "warn");
}

async function copyExportedFiles(result) {
  if (!result.files?.length) return { copied: [], errors: [] };
  // 浏览器不支持 File System Access API 时，不尝试复制到本地目录，仅保留 downloadUrls 下载链接。
  if (exportFallbackToDownload) {
    return { copied: [], errors: [] };
  }
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
    // P2-33：相对路径服务端会直接拒绝，先在前端拦下并给出可操作的提示
    const pathProblem = exportTargetPathProblem();
    if (pathProblem) {
      setStatus(pathProblem, "error");
      return;
    }
    const targetReady = await ensureExportTargetReady();
    setStatus("正在导出...");
    const result = await requestJson("/api/export/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    const copyResult = targetReady.ok ? await copyExportedFiles(result) : { copied: [], errors: [] };
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
    } else if (copyResult.copied.length) {
      setStatus(`${result.message} 已复制到你选择的位置。`, "success");
    } else if (targetReady.reason === "server") {
      // 目标由服务端直接写入用户填写的绝对路径（已完成，不需要前端复制）
      setStatus(`${result.message} 已按设置写入：${result.files[0] || targetReady.serverPath}`, "success");
    } else if (targetReady.reason === "missing") {
      // 任务里既没有句柄（浏览器直选无法随任务跨浏览器保存），也没填绝对路径
      setStatus(`${result.message} 文件已生成在服务器默认导出目录。该任务未记住目标位置：请点「...」重选文件夹，或直接填写绝对路径后保存任务。`, "warn");
    } else if (targetReady.reason === "denied") {
      setStatus(`${result.message} 文件已生成，但浏览器未授权写入“${targetReady.name}”，已跳过复制。请重新点「...」选择该文件夹后再导出。`, "warn");
    } else {
      setStatus(result.message, "success");
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
  const taskName = ($("#exportTaskName")?.value || "").trim();
  if (!taskName) {
    setStatus("请填写任务名称。", "error");
    return null;
  }
  // P2-33：相对路径存进任务后，服务端每次导出都会报错，这里提前拦下
  const pathProblem = exportTargetPathProblem();
  if (pathProblem) {
    setStatus(pathProblem, "error");
    return null;
  }
  const config = collectPayload();
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
  // P2-33：新任务保存成功，把「尚未保存」时记录的句柄迁移到这个任务 id 下
  if (!existingJob) await migrateExportTargetRecord(result.job.id);
  await loadExportTaskJobs();
  clearExportDraft();
  setStatus(`${existingJob ? "已更新" : "已保存"}导出任务：${result.job.name}，可在定时任务中调用。`, "success");
  return result.job;
}

function isExportTaskJob(job) {
  // 只认"单步导出任务"：导出任务 = 仅一个 export 步骤的资产。
  // 多步作业即使首步是 export 也不应混入导出任务列表。
  return (job.steps || []).length === 1 && (job.steps || [])[0]?.type === "export";
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
        <button id="backExportTaskList" type="button">返回任务列表</button>
        <button id="openExportTask" type="button" disabled>打开导出</button>
        <button id="newExportTask" type="button">新增导出</button>
        <button id="deleteExportTask" type="button" disabled>删除导出</button>
        <span id="exportTaskHint">当前模块保存的导出任务</span>
      </div>
      <div id="exportTaskList" class="module-task-list empty">暂无导出任务</div>`;
    shell.insertAdjacentElement("afterbegin", panel);
    document.querySelector("#openExportTask").addEventListener("click", () => openSelectedExportTask().catch((error) => setStatus(error.message, "error")));
    // 与导入模块对齐：第三个按钮在主览/编辑态间切换为「新增导出 / 保存修改 / 保存为新任务」，
    // 保存入口不再依赖已隐藏的顶栏。
    document.querySelector("#newExportTask").addEventListener("click", () => {
      if (exportToolbarAction() === "browse") {
        startNewExportTask();
      } else {
        saveExportTask().catch((error) => setStatus(error.message, "error"));
      }
    });
    document.querySelector("#deleteExportTask").addEventListener("click", () => deleteSelectedExportTask().catch((error) => setStatus(error.message, "error")));
    // P2-31：同导入模块——编辑器模式没有返回总览的入口，补一个显式返回按钮。
    document.querySelector("#backExportTaskList").addEventListener("click", () => {
      selectedExportTaskId = "";
      setExportEditorVisible(false);
      loadExportTaskJobs().catch((error) => setStatus(error.message, "error"));
    });
  }
}

// 工具条第三个按钮要做的事，随状态切换（与导入模块同一套交互，但补上"新建后保存"这一态）：
//   browse —— 总览态：新增导出
//   update —— 编辑态且正在编辑一条已保存任务：保存修改（覆盖该任务）
//   create —— 编辑态且没有选中任务（刚点「新增导出」）：保存为新任务
// 为什么必须区分 browse/update：只在总览里单击选中一条任务时，表单里还是"上一个任务"的内容，
// 若此时允许保存，会把旧表单连名字一起覆盖到刚点中的那条任务上（数据丢失）。
function exportToolbarAction() {
  const shell = document.querySelector(".export-shell");
  if (!shell || shell.classList.contains("task-overview-mode")) return "browse";
  const job = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  return job ? "update" : "create";
}

function exportToolbarLabel(action) {
  return action === "update" ? "保存修改" : action === "create" ? "保存为新任务" : "新增导出";
}

function updateExportTaskSelection() {
  const hasSelection = Boolean(selectedExportTaskId && exportTaskJobs.some((job) => job.id === selectedExportTaskId));
  document.querySelectorAll("#exportTaskList [data-id]").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === selectedExportTaskId);
  });
  const openButton = document.querySelector("#openExportTask");
  const newButton = document.querySelector("#newExportTask");
  const deleteButton = document.querySelector("#deleteExportTask");
  if (openButton) openButton.disabled = !hasSelection;
  if (newButton) newButton.textContent = exportToolbarLabel(exportToolbarAction());
  if (deleteButton) deleteButton.disabled = !hasSelection;
}

function renderExportTaskJobs() {
  ensureExportTaskPanel();
  const list = document.querySelector("#exportTaskList");
  if (!list) return;
  if (!exportTaskJobs.length) {
    list.className = "module-task-list empty";
    list.textContent = "暂无导出任务";
  } else {
    list.className = "module-task-list";
    list.innerHTML = exportTaskJobs
      .map((job) => `<button type="button" class="module-task-item task-export ${job.id === selectedExportTaskId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><span class="task-type-icon">OUT</span><span class="task-item-name">${escapeHtml(job.name)}</span></button>`)
      .join("");
  }
  document.querySelectorAll("#exportTaskList [data-id]").forEach((button) => {
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
    // 快照同样只给名称，不展示主机 / 库名。
    option.textContent = config.dbHost ? "保存的连接快照" : "连接已丢失，请重新选择";
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

// P2-33：打开任务后恢复上次选择的导出目标。
// 恢复顺序：任务 id 下的 IndexedDB 句柄 → 配置里的绝对路径（由 applyExportTaskConfig 回填）。
// 句柄权限只做 queryPermission（不弹窗）；真正写文件时在 runExport 的用户手势里再请求。
async function restoreExportTarget(job) {
  if (!job?.id) return null;
  const mode = radioValue("exportTargetMode") || "folder";
  // 配置里已经有服务端绝对路径时优先用它：服务端会直接写入该目录，定时任务也能生效，
  // 不能被旧的浏览器句柄记录覆盖成"已选择文件夹：xxx"（那样反而看不到路径）。
  if (normalizeServerPath(mode === "file" ? $("#outputName").value : $("#exportFolder").value)) return null;
  let record = null;
  try {
    record = await exportTargetDbGet(job.id);
  } catch (error) {
    console.warn("读取导出目标记录失败:", error);
    return null;
  }
  if (!record) return null;
  const handle = mode === "file" ? record.fileHandle : record.folderHandle;
  const name = mode === "file" ? record.fileName : record.folderName;
  if (!handle || !name) return null;
  const permission = await ensureHandlePermission(handle, "readwrite", { request: false });
  if (mode === "file") {
    exportFileHandle = handle;
    exportDirectoryHandle = null;
    $("#outputName").value = `已选择文件：${name}`;
  } else {
    exportDirectoryHandle = handle;
    exportFileHandle = null;
    $("#exportFolder").value = `已选择文件夹：${name}`;
  }
  return { name, mode, permission };
}

async function openSelectedExportTask() {
  const job = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  if (!job) return;
  const step = exportTaskStep(job);
  // 打开已有任务后草稿不再有复用价值，避免下次「新增导出」误带旧数据。
  pendingExportDraft = null;
  resetExportEditor();
  setExportEditorVisible(true);
  clearExportDraft();
  const nameInput = document.querySelector("#exportTaskName");
  if (nameInput) nameInput.value = job.name || "";
  await applyExportTaskConfig(step.config || {});
  const restored = await restoreExportTarget(job);
  if (restored && restored.permission === "granted") {
    setStatus(`已打开导出任务：${job.name}，已恢复导出${restored.mode === "file" ? "文件" : "文件夹"}“${restored.name}”。`, "success");
  } else if (restored) {
    setStatus(`已打开导出任务：${job.name}，已记住导出${restored.mode === "file" ? "文件" : "文件夹"}“${restored.name}”，首次导出时会请求一次写入授权。`, "warn");
  } else if (radioValue("exportTargetMode") === "folder" && !$("#exportFolder").value.trim()) {
    setStatus(`已打开导出任务：${job.name}。该任务未设置目标文件夹，导出将落在服务器默认目录；如需固定位置，请点「...」选择（会显示完整路径）后重新保存任务。`, "warn");
  } else {
    const targetText = normalizeServerPath(radioValue("exportTargetMode") === "file" ? $("#outputName").value : $("#exportFolder").value);
    setStatus(targetText ? `已打开导出任务：${job.name}，导出目标：${targetText}` : `已打开导出任务：${job.name}`, "success");
  }
}

async function deleteSelectedExportTask() {
  const job = exportTaskJobs.find((item) => item.id === selectedExportTaskId);
  if (!job) return;
  if (!window.confirm(`确定删除导出任务“${job.name}”吗？关联的定时任务也会一起删除。`)) return;
  await requestJson(`/api/jobs?id=${encodeURIComponent(job.id)}`, { method: "DELETE" });
  await forgetExportTarget(job.id);
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
// P2-33：手改目标路径即视为改用「绝对路径」方式，丢弃浏览器直选留下的句柄，避免两处同时落盘。
$("#exportFolder").addEventListener("input", (event) => {
  if (!String(event.target.value || "").startsWith("已选择文件夹：")) {
    exportDirectoryHandle = null;
    exportFolderServerValue = "";
  }
});
$("#outputName").addEventListener("input", (event) => {
  if (!String(event.target.value || "").startsWith("已选择文件：")) {
    exportFileHandle = null;
    outputNameServerValue = "";
  }
});
$("#previewExport").addEventListener("click", previewExport);
$("#runExport").addEventListener("click", runExport);
$("#startExport").addEventListener("click", runExport);
$("#stopExport").addEventListener("click", () => setStatus("当前导出任务为同步执行，暂无运行中的任务。", "warn"));
$("#saveExportConfig").addEventListener("click", () => saveExportTask().catch((error) => setStatus(error.message, "error")));
$("#explainExport").addEventListener("click", () => setStatus("不支持的 .xls、DBF 和系统自动打开文件夹已在页面禁用。", "warn"));

ensureExportTaskPanel();
setExportEditorVisible(false);

// P2-16：导出编辑器草稿。配置实时存 localStorage，刷新后自动恢复；
// 保存为任务、打开任务或新建任务后清除。文件夹/文件直选句柄和密码类字段不落盘。
const EXPORT_DRAFT_KEY = "dc_export_draft_v1";

function collectExportDraft() {
  const draft = {
    values: {},
    checks: {},
    radios: {},
    sourceMode,
    connectionId: exportConnection.value,
    // 表模式下勾选的表名单独记录（勾选框无 id，且表列表需异步加载后才能重建）
    selectedTables: sourceMode === "table"
      ? [...exportSourceList.querySelectorAll("input[type='checkbox']:checked")].map((input) => input.value)
      : [],
    activeExportTab: document.querySelector("[data-export-tab].active")?.dataset.exportTab || "data",
  };
  document.querySelectorAll(".export-left input, .export-left select, .export-left textarea, .export-right input, .export-right select, .export-right textarea").forEach((control) => {
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

function saveExportDraftNow() {
  try {
    localStorage.setItem(EXPORT_DRAFT_KEY, JSON.stringify(collectExportDraft()));
  } catch (_) {
    // 存储满或被禁用时静默放弃，不影响正常导出流程。
  }
}

let exportDraftTimer = 0;
// 用户编辑过后才允许 pagehide 兜底保存；保存/打开/新建任务主动清草稿后不再回写
let exportDraftDirty = false;

function scheduleExportDraftSave() {
  exportDraftDirty = true;
  clearTimeout(exportDraftTimer);
  exportDraftTimer = setTimeout(saveExportDraftNow, 400);
}

function clearExportDraft() {
  exportDraftDirty = false;
  try {
    localStorage.removeItem(EXPORT_DRAFT_KEY);
  } catch (_) {
    // 忽略
  }
}

function applyExportDraftValues(draft) {
  if (!draft || typeof draft !== "object") return;
  // 恢复连接选择（须在连接列表加载完成后调用）
  if (draft.connectionId && [...exportConnection.options].some((option) => option.value === draft.connectionId)) {
    exportConnection.value = draft.connectionId;
  }
  Object.entries(draft.values || {}).forEach(([id, value]) => {
    const control = document.querySelector(`#${CSS.escape(id)}`);
    if (control && control.value !== value) {
      control.value = value;
    }
  });
  Object.entries(draft.checks || {}).forEach(([id, checked]) => {
    const control = document.querySelector(`#${CSS.escape(id)}`);
    if (control && control.type === "checkbox" && control.checked !== Boolean(checked)) {
      control.checked = Boolean(checked);
    }
  });
  Object.entries(draft.radios || {}).forEach(([name, value]) => {
    setRadioValue(name, value);
  });
}

// P2-33：草稿对应「尚未保存的新任务」，其直选句柄存在 __draft__ 键下，这里顺带恢复绑定。
// 只绑句柄、不改输入框文本（草稿已经把「已选择文件夹：xxx」回填好了）。
async function restoreDraftExportTarget(draft) {
  try {
    const record = await exportTargetDbGet(EXPORT_TARGET_DRAFT_KEY);
    if (!record) return;
    const mode = draft?.radios?.exportTargetMode || radioValue("exportTargetMode") || "folder";
    const handle = mode === "file" ? record.fileHandle : record.folderHandle;
    const name = mode === "file" ? record.fileName : record.folderName;
    if (!handle || !name) return;
    await ensureHandlePermission(handle, "readwrite", { request: false });
    if (mode === "file") {
      exportFileHandle = handle;
      exportDirectoryHandle = null;
    } else {
      exportDirectoryHandle = handle;
      exportFileHandle = null;
    }
  } catch (error) {
    console.warn("恢复草稿导出目标失败:", error);
  }
}

async function restoreExportDraft() {
  let raw = "";
  try {
    raw = localStorage.getItem(EXPORT_DRAFT_KEY) || "";
  } catch (_) {
    return false;
  }
  if (!raw) return false;
  let draft;
  try {
    draft = JSON.parse(raw);
  } catch (_) {
    clearExportDraft();
    return false;
  }
  if (!draft || typeof draft !== "object") return false;
  applyExportDraftValues(draft);
  // 恢复导出对象模式：表模式需先拉取表列表，再回填勾选状态
  if (draft.sourceMode === "table") {
    sourceMode = "table";
    try {
      await loadSources();
      const tableNames = new Set(Array.isArray(draft.selectedTables) ? draft.selectedTables : []);
      let anyChecked = false;
      exportSourceList.querySelectorAll("input[type='checkbox']").forEach((input) => {
        input.checked = tableNames.has(input.value);
        if (input.checked) anyChecked = true;
      });
      if (!anyChecked && exportSourceList.querySelector("input[type='checkbox']")) {
        // 草稿里的表已不存在时兜底勾选第一张，避免空选择
        exportSourceList.querySelector("input[type='checkbox']").checked = true;
      }
    } catch (_) {
      exportSourceList.classList.remove("hidden");
      exportSourceList.textContent = "读取导出对象列表失败，请点击“选择表”重新加载并勾选。";
    }
  } else if (draft.sourceMode === "multi") {
    sourceMode = "multi";
    exportSourceList.textContent = "当前使用多个 SQL 查询，使用分号分隔";
    exportSourceList.classList.add("hidden");
  } else {
    sourceMode = "query";
    exportSourceList.textContent = "当前使用单个 SQL 查询";
    exportSourceList.classList.add("hidden");
  }
  const activeTab = draft.activeExportTab || "data";
  document.querySelectorAll("[data-export-tab]").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.exportTab === activeTab);
  });
  document.querySelectorAll("[data-export-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.exportPanel === activeTab);
  });
  // P2-33：恢复直选的导出目标句柄（存在 IndexedDB，草稿本身存不了句柄）
  await restoreDraftExportTarget(draft);
  // P2-32：只回填、不切视图。打开模块先看到任务列表，草稿留给「新增导出」复用。
  pendingExportDraft = draft;
  return true;
}

const exportShell = document.querySelector("main.export-shell");
exportShell?.addEventListener("input", scheduleExportDraftSave);
exportShell?.addEventListener("change", scheduleExportDraftSave);
// 刷新/关闭前的兜底：防抖未到点时立即落盘，避免"输入后马上刷新"丢草稿
window.addEventListener("pagehide", () => {
  if (!exportDraftDirty) return;
  saveExportDraftNow();
});

loadConnections()
  .then(() => {
    sourceMode = "query";
    exportSourceList.textContent = "当前使用单个 SQL 查询";
    exportSourceList.classList.add("hidden");
    exportSql.value = exportSql.value || "select 1 as value";
    loadExportTaskJobs().catch((error) => setStatus(error.message, "error"));
    if (applyPendingQueryExport()) return;
    restoreExportDraft()
      .then((restored) => {
        if (restored) {
          setStatus("已恢复上次未保存的导出配置草稿，点击「新增导出」可继续编辑。", "warn");
        } else {
          setStatus("已进入 SQL 查询导出模式。可直接预览或开始导出。", "success");
        }
      })
      .catch((error) => setStatus(error.message, "error"));
  })
  .catch((error) => setStatus(error.message, "error"));
