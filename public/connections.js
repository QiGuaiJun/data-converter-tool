const list = document.querySelector("#connectionList");
const form = document.querySelector("#connectionForm");
const statusBox = document.querySelector("#connStatus");
const editorTitle = document.querySelector("#editorTitle");

let connections = [];

function $(selector) {
  return document.querySelector(selector);
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
  statusBox.textContent = message;
  statusBox.className = type;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "请求失败。");
  }
  return payload;
}

function payloadFromForm() {
  return {
    id: $("#connId").value,
    name: $("#connName").value,
    dbType: $("#connDbType").value,
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

function resetForm() {
  editorTitle.textContent = "新建连接";
  form.reset();
  $("#connId").value = "";
  $("#connDbType").value = "mysql";
  $("#connHost").value = "192.168.1.102";
  $("#connPort").value = "3306";
  $("#connUser").value = "root";
  $("#connPassword").value = "";
  $("#connDatabase").innerHTML = '<option value="lcdp_SR">lcdp_SR</option>';
  $("#connCharset").value = "utf8mb4";
  setStatus("等待操作");
}

function fillForm(item) {
  editorTitle.textContent = `编辑连接：${item.name}`;
  $("#connId").value = item.id || "";
  $("#connName").value = item.name || "";
  $("#connDbType").value = item.dbType || "mysql";
  $("#connHost").value = item.host || "";
  $("#connPort").value = item.port || "3306";
  $("#connUser").value = item.user || "";
  $("#connPassword").value = "";
  const database = item.database || "";
  $("#connDatabase").innerHTML = `<option value="${escapeHtml(database)}">${escapeHtml(database)}</option>`;
  $("#connDatabase").value = database;
  $("#connCharset").value = item.charset || "utf8mb4";
  $("#connSslEnabled").checked = Boolean(item.sslEnabled);
  $("#connSslCa").value = item.sslCa || "";
  $("#connSslCert").value = item.sslCert || "";
  $("#connSslKey").value = item.sslKey || "";
  setStatus("已载入连接。", "success");
}

function renderList() {
  list.innerHTML = connections.length ? "" : '<div class="empty-list">暂无连接</div>';
  for (const item of connections) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "connection-list-item";
    button.innerHTML = `<strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.host)} / ${escapeHtml(item.database)}</span>`;
    button.addEventListener("click", () => fillForm(item));
    list.append(button);
  }
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
  renderList();
}

async function testConnection() {
  setStatus("正在测试连接...");
  const payload = await requestJson("/api/connections/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payloadFromForm()),
  });
  $("#connDatabase").innerHTML = payload.databases.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  if (payload.databases.includes("lcdp_SR")) {
    $("#connDatabase").value = "lcdp_SR";
  }
  setStatus(`连接成功：MySQL ${payload.version}`, "success");
}

async function saveConnection(event) {
  event.preventDefault();
  setStatus("正在保存连接...");
  const payload = await requestJson("/api/connections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payloadFromForm()),
  });
  await loadConnections();
  fillForm(payload.connection);
  setStatus("连接已保存。", "success");
}

async function deleteConnection() {
  const id = $("#connId").value;
  if (!id) {
    setStatus("请先选择要删除的连接。", "warn");
    return;
  }
  if (!window.confirm("确认删除当前连接？")) return;
  await requestJson(`/api/connections?id=${encodeURIComponent(id)}`, { method: "DELETE" });
  resetForm();
  await loadConnections();
  setStatus("连接已删除。", "success");
}

$("#newConn").addEventListener("click", resetForm);
$("#testConn").addEventListener("click", () => testConnection().catch((error) => setStatus(error.message, "error")));
$("#deleteConn").addEventListener("click", () => deleteConnection().catch((error) => setStatus(error.message, "error")));
form.addEventListener("submit", (event) => saveConnection(event).catch((error) => setStatus(error.message, "error")));

loadConnections().catch((error) => setStatus(error.message, "error"));
