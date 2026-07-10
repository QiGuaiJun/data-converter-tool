const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let jobs = [];
let connections = [];
let selectedJobId = "";
let editingJobId = "";
let draftSteps = [];
let selectedStepIndex = -1;

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || "请求失败");
  return payload;
}

function setStatus(message, type = "") {
  $("#jobStatus").textContent = message;
  $("#jobStatus").className = type;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function connectionFields() {
  const id = $("#jobConnection").value;
  if (!id || id === "__sqlite") return { targetDbType: "sqlite" };
  return { connectionId: id, targetDbType: "mysql" };
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
  $("#jobConnection").innerHTML =
    '<option value="__sqlite">本地 SQLite</option>' +
    connections.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (${escapeHtml(item.host)}/${escapeHtml(item.database)})</option>`).join("");
}

async function loadJobs() {
  const payload = await requestJson("/api/jobs");
  jobs = payload.jobs || [];
  renderJobs();
  if (selectedJobId && !jobs.some((item) => item.id === selectedJobId)) selectedJobId = "";
  if (!selectedJobId && jobs[0]) selectedJobId = jobs[0].id;
  renderSelectedJob();
}

async function loadRuns() {
  const query = selectedJobId ? `?jobId=${encodeURIComponent(selectedJobId)}` : "";
  const payload = await requestJson(`/api/job-runs${query}`);
  $("#jobRuns").innerHTML = (payload.runs || []).length
    ? payload.runs
        .map((run) => `<div class="log-item ${run.status === "成功" ? "success" : "failed"}"><strong>${escapeHtml(run.job_name)}<span>${escapeHtml(run.status)}</span></strong><div>${escapeHtml(run.started_at)} · ${escapeHtml(run.elapsed_ms)} ms</div><div>${escapeHtml(run.message)}</div>${(run.steps || []).map((step) => `<small>${step.step_index}. ${escapeHtml(step.step_name)} ${escapeHtml(step.status)} ${escapeHtml(step.message)}</small>`).join("")}</div>`)
        .join("")
    : "暂无日志";
}

function renderJobs() {
  $("#jobList").innerHTML = jobs.length
    ? jobs.map((job) => `<button class="job-item ${job.id === selectedJobId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><strong>${escapeHtml(job.name)}</strong><span>${job.steps.length} 个步骤 · ${escapeHtml(job.updatedAt)}</span></button>`).join("")
    : '<div class="empty-list">暂无作业</div>';
  $$("#jobList .job-item").forEach((button) =>
    button.addEventListener("click", () => {
      selectedJobId = button.dataset.id;
      renderJobs();
      renderSelectedJob();
    }),
  );
}

function renderSelectedJob() {
  const job = jobs.find((item) => item.id === selectedJobId);
  $("#stepPreview").innerHTML = job
    ? job.steps.map((step, index) => `<div class="step-row"><strong>${index + 1}. ${escapeHtml(step.name)}</strong><span>${escapeHtml(step.type)} · ${step.enabled ? "启用" : "禁用"} · ${step.continueOnError ? "失败继续" : "失败停止"}</span></div>`).join("")
    : "请选择作业";
  loadRuns().catch((error) => setStatus(error.message, "error"));
}

function renderStepConfig() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  const connectionHint = `<p class="hint">连接会随子任务一起保存。</p>`;
  if (type === "import") {
    $("#stepConfig").innerHTML = `${connectionHint}<label>步骤名称：<input id="stepName" value="导入数据" /></label><label>文件或目录路径：<input id="importPath" placeholder="例如 C:\\data\\inbox" /></label><label>目标表：<input id="importTable" placeholder="可为空，按文件名生成" /></label><label>导入模式：<select id="importMode"><option value="append">追加</option><option value="update">更新</option><option value="overwrite">覆盖</option><option value="rebuild">重建</option></select></label><label><input id="stepContinue" type="checkbox" /> 失败后继续</label>`;
  } else if (type === "export") {
    $("#stepConfig").innerHTML = `${connectionHint}<label>步骤名称：<input id="stepName" value="导出数据" /></label><label>导出 SQL：<textarea id="exportSql" placeholder="select * from table_name"></textarea></label><label>结果名称：<input id="exportName" value="job_export" /></label><label>文件格式：<select id="exportExt"><option value="xlsx">xlsx</option><option value="csv">csv</option><option value="json">json</option><option value="xml">xml</option><option value="txt">txt</option></select></label><label>Sheet 名称：<input id="sheetName" value="Sheet1" /></label><label><input id="stepContinue" type="checkbox" /> 失败后继续</label>`;
  } else if (type === "query") {
    $("#stepConfig").innerHTML = `${connectionHint}<label>步骤名称：<input id="stepName" value="执行查询" /></label><label>SQL：<textarea id="querySql" placeholder="select 1"></textarea></label><label><input id="stepContinue" type="checkbox" /> 失败后继续</label>`;
  } else if (type === "job") {
    const options = jobs.filter((job) => job.id !== editingJobId).map((job) => `<option value="${escapeHtml(job.id)}">${escapeHtml(job.name)}</option>`).join("");
    $("#stepConfig").innerHTML = `<label>步骤名称：<input id="stepName" value="执行子作业" /></label><label>选择作业：<select id="nestedJob">${options}</select></label><label><input id="stepContinue" type="checkbox" /> 失败后继续</label>`;
  }
}

function draftStepFromForm() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  if (type === "sync") throw new Error("同步模块开发后开放。");
  const base = { id: crypto.randomUUID(), type, name: $("#stepName").value || "未命名步骤", enabled: true, continueOnError: $("#stepContinue")?.checked || false, config: {} };
  if (type === "import") base.config = { ...connectionFields(), path: $("#importPath").value, tableName: $("#importTable").value, importMode: $("#importMode").value, fieldCase: "lower", tableCase: "lower" };
  if (type === "export") base.config = { ...connectionFields(), items: [{ type: "query", name: $("#exportName").value || "job_export", sql: $("#exportSql").value }], extension: $("#exportExt").value, outputName: $("#exportName").value || "job_export", sheetName: $("#sheetName").value, headerMode: "field", exportMode: "workbook" };
  if (type === "query") base.config = { ...connectionFields(), sql: $("#querySql").value };
  if (type === "job") base.config = { jobId: $("#nestedJob").value };
  return base;
}

function renderDraftSteps() {
  $("#selectedSteps").innerHTML = draftSteps.length
    ? draftSteps.map((step, index) => `<button type="button" class="selected-step ${index === selectedStepIndex ? "active" : ""}" data-index="${index}"><strong>${index + 1}. ${escapeHtml(step.name)}</strong><span>${escapeHtml(step.type)} · ${step.continueOnError ? "失败继续" : "失败停止"}</span></button>`).join("")
    : '<div class="empty-list">还没有子任务</div>';
  $$("#selectedSteps .selected-step").forEach((button) => button.addEventListener("click", () => { selectedStepIndex = Number(button.dataset.index); renderDraftSteps(); }));
}

function openJobDialog(job = null) {
  editingJobId = job?.id || "";
  $("#jobDialogTitle").textContent = job ? "编辑作业" : "新增作业";
  $("#jobName").value = job?.name || "";
  draftSteps = job ? JSON.parse(JSON.stringify(job.steps)) : [];
  selectedStepIndex = draftSteps.length ? 0 : -1;
  renderStepConfig();
  renderDraftSteps();
  $("#jobDialog").showModal();
}

async function saveJob() {
  const payload = { id: editingJobId, name: $("#jobName").value, enabled: true, steps: draftSteps };
  await requestJson("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  $("#jobDialog").close();
  await loadJobs();
  setStatus("作业已保存。", "success");
}

async function runSelectedJob() {
  if (!selectedJobId) throw new Error("请先选择作业。");
  setStatus("正在执行作业...");
  const payload = await requestJson("/api/jobs/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: selectedJobId }) });
  setStatus(`${payload.run.status}：${payload.run.message}`, payload.run.status === "成功" ? "success" : "error");
  await loadRuns();
}

$("#newJob").addEventListener("click", () => openJobDialog());
$("#editJob").addEventListener("click", () => { const job = jobs.find((item) => item.id === selectedJobId); if (job) openJobDialog(job); });
$("#deleteJob").addEventListener("click", async () => { if (!selectedJobId || !confirm("确认删除当前作业？")) return; await requestJson(`/api/jobs?id=${encodeURIComponent(selectedJobId)}`, { method: "DELETE" }); selectedJobId = ""; await loadJobs(); });
$("#runJob").addEventListener("click", () => runSelectedJob().catch((error) => setStatus(error.message, "error")));
$("#refreshJobs").addEventListener("click", () => Promise.all([loadJobs(), loadRuns()]));
$("#closeJobDialog").addEventListener("click", () => $("#jobDialog").close());
$("#cancelJob").addEventListener("click", () => $("#jobDialog").close());
$("#saveJob").addEventListener("click", () => saveJob().catch((error) => setStatus(error.message, "error")));
$("#addStep").addEventListener("click", () => { try { draftSteps.push(draftStepFromForm()); selectedStepIndex = draftSteps.length - 1; renderDraftSteps(); } catch (error) { setStatus(error.message, "error"); } });
$("#removeStep").addEventListener("click", () => { if (selectedStepIndex >= 0) draftSteps.splice(selectedStepIndex, 1); selectedStepIndex = Math.min(selectedStepIndex, draftSteps.length - 1); renderDraftSteps(); });
$("#moveStepUp").addEventListener("click", () => { if (selectedStepIndex > 0) { [draftSteps[selectedStepIndex - 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex - 1]]; selectedStepIndex -= 1; renderDraftSteps(); } });
$("#moveStepDown").addEventListener("click", () => { if (selectedStepIndex >= 0 && selectedStepIndex < draftSteps.length - 1) { [draftSteps[selectedStepIndex + 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex + 1]]; selectedStepIndex += 1; renderDraftSteps(); } });
$$('input[name="stepType"]').forEach((item) => item.addEventListener("change", renderStepConfig));

Promise.all([loadConnections(), loadJobs()]).catch((error) => setStatus(error.message, "error"));
