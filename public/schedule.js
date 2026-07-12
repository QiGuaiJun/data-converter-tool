const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let jobs = [];
let schedules = [];
let connections = [];
let selectedScheduleId = "";
let editingScheduleId = "";
let editingJobId = "";
let draftSteps = [];
let selectedStepIndex = -1;
let selectedAvailableJobId = "";
let draftRule = { mode: "interval", amount: 1, unit: "hours" };

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || "请求失败");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function setStatus(message, type = "") {
  $("#scheduleStatus").textContent = message;
  $("#scheduleStatus").className = type;
}

function normalizeDateTimeValue(value, fallback = "") {
  if (!value) return fallback;
  return String(value).replace(" ", "T").slice(0, 19);
}

function localDateValue(offsetMinutes = 0) {
  const date = new Date(Date.now() + offsetMinutes * 60000);
  date.setMilliseconds(0);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function connectionFields() {
  const id = $("#scheduleConnection").value;
  if (!id || id === "__sqlite") return { targetDbType: "sqlite" };
  return { connectionId: id, targetDbType: "mysql" };
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
  $("#scheduleConnection").innerHTML =
    '<option value="__sqlite">本地 SQLite</option>' +
    connections.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (${escapeHtml(item.host)}/${escapeHtml(item.database)})</option>`).join("");
}

async function loadJobs() {
  const payload = await requestJson("/api/jobs");
  jobs = payload.jobs || [];
}

async function loadSchedules() {
  const payload = await requestJson("/api/schedules");
  schedules = payload.schedules || [];
  if (selectedScheduleId && !schedules.some((item) => item.id === selectedScheduleId)) selectedScheduleId = "";
  if (!selectedScheduleId && schedules[0]) selectedScheduleId = schedules[0].id;
  renderSchedules();
}

async function refreshAll() {
  await Promise.all([loadConnections(), loadJobs(), loadSchedules()]);
}

function selectedSchedule() {
  return schedules.find((item) => item.id === selectedScheduleId);
}

function jobForSchedule(schedule) {
  return jobs.find((job) => job.id === schedule?.jobId);
}

function statusText(item) {
  if (!item) return "";
  if (item.running) return "运行中";
  return item.enabled ? "准备就绪" : "已禁用";
}

function lastResultText(item) {
  if (!item?.lastRunAt) return "";
  return item.lastStatus || "";
}

function renderSchedules() {
  const body = $("#scheduleTableBody");
  body.innerHTML = schedules.length
    ? schedules
        .map(
          (item) => `<tr class="${item.id === selectedScheduleId ? "selected" : ""}" data-id="${escapeHtml(item.id)}">
            <td><span class="schedule-clock">◷</span>${escapeHtml(item.name)}</td>
            <td>${escapeHtml(statusText(item))}</td>
            <td>${escapeHtml(ruleText(item.rule))}</td>
            <td>${escapeHtml(item.nextRunAt || "")}</td>
            <td>${escapeHtml(item.lastRunAt || "")}</td>
            <td>${escapeHtml(lastResultText(item))}</td>
          </tr>`,
        )
        .join("")
    : '<tr><td colspan="6" class="schedule-empty">暂无定时任务</td></tr>';

  $$("#scheduleTableBody tr[data-id]").forEach((row) =>
    row.addEventListener("click", () => {
      selectedScheduleId = row.dataset.id;
      renderSchedules();
    }),
  );
  updateToolbarState();
}

function updateToolbarState() {
  const item = selectedSchedule();
  const hasSelection = Boolean(item);
  $("#editSchedule").disabled = !hasSelection;
  $("#deleteSchedule").disabled = !hasSelection;
  $("#viewScheduleLog").disabled = !hasSelection;
  $("#runScheduleNow").disabled = !hasSelection;
  $("#startSchedule").disabled = !hasSelection || item.enabled;
  $("#pauseSchedule").disabled = !hasSelection || !item.enabled;
}

function unitText(unit) {
  return { seconds: "秒", minutes: "分钟", hours: "小时", days: "天" }[unit] || "分钟";
}

function ruleText(rule = {}) {
  if (rule.mode === "interval") return `每 ${rule.amount || 1} ${unitText(rule.unit)}轮询`;
  if (rule.mode === "daily") return `每天 ${rule.time || "09:00:00"}`;
  if (rule.mode === "weekly") return `每周第 ${rule.weekday || 1} 天 ${rule.time || "09:00:00"}`;
  if (rule.mode === "monthly") return `每月第 ${rule.day || 1} 天 ${rule.time || "09:00:00"}`;
  if (rule.mode === "yearly") return `每年第 ${rule.month || 1} 月第 ${rule.day || 1} 天 ${rule.time || "09:00:00"}`;
  return "运行一次";
}

function updateRuleSummary() {
  $("#ruleSummary").textContent = ruleText(draftRule);
}

function jobPrimaryType(job) {
  const steps = job?.steps || [];
  if (steps.length === 1 && steps[0].type === "job") {
    const nestedId = steps[0].config?.jobId;
    const nested = jobs.find((item) => item.id === nestedId);
    return nested ? jobPrimaryType(nested) : "job";
  }
  const types = [...new Set(steps.map((step) => step.type))];
  if (types.length === 1) return types[0];
  return types.length ? "job" : "";
}

function isScheduleBackingJob(job) {
  return String(job?.name || "").endsWith(" - 自动作业");
}

function candidateJobsForType(type) {
  return jobs.filter((job) => {
    if (job.id === editingJobId) return false;
    if (isScheduleBackingJob(job)) return false;
    if (type === "job") return true;
    return jobPrimaryType(job) === type;
  });
}

function renderStepConfig() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  const candidates = candidateJobsForType(type);
  if (!candidates.some((job) => job.id === selectedAvailableJobId)) {
    selectedAvailableJobId = candidates[0]?.id || "";
  }
  $("#stepConfig").innerHTML = candidates.length
    ? candidates
        .map((job) => `<button type="button" class="available-job ${job.id === selectedAvailableJobId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><strong>${escapeHtml(job.name)}</strong><span>${escapeHtml(jobKindText(job))}</span></button>`)
        .join("")
    : `<div class="empty-list">暂无${typeText(type)}作业，请先在${typeText(type)}页面保存作业</div>`;
  $$("#stepConfig .available-job").forEach((button) =>
    button.addEventListener("click", () => {
      selectedAvailableJobId = button.dataset.id;
      renderStepConfig();
    }),
  );
}

function jobKindText(job) {
  const types = [...new Set((job.steps || []).map((step) => typeText(step.type)))];
  return types.length ? types.join("+") : "作业";
}

function draftStepFromForm() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  if (type === "sync") throw new Error("同步模块尚未开放。");
  const base = { id: crypto.randomUUID(), type, name: $("#stepName").value || "未命名步骤", enabled: true, continueOnError: $("#stepContinue")?.checked || false, config: {} };
  if (type === "import") base.config = { ...connectionFields(), path: $("#importPath").value, tableName: $("#importTable").value, importMode: $("#importMode").value, fieldCase: "lower", tableCase: "lower" };
  if (type === "export") base.config = { ...connectionFields(), items: [{ type: "query", name: $("#exportName").value || "job_export", sql: $("#exportSql").value }], extension: $("#exportExt").value, outputName: $("#exportName").value || "job_export", sheetName: $("#sheetName").value, headerMode: "field", exportMode: "workbook" };
  if (type === "query") base.config = { ...connectionFields(), sql: $("#querySql").value };
  if (type === "job") base.config = { jobId: $("#nestedJob").value };
  return base;
}

function addSelectedAvailableJob() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  if (type === "sync") throw new Error("同步模块尚未开放。");
  const job = jobs.find((item) => item.id === selectedAvailableJobId);
  if (!job) throw new Error(`请先选择一个${typeText(type)}作业。`);
  draftSteps.push({
    id: crypto.randomUUID(),
    type: "job",
    name: job.name,
    enabled: true,
    continueOnError: false,
    config: { jobId: job.id },
  });
  selectedStepIndex = draftSteps.length - 1;
  renderDraftSteps();
}

function renderDraftSteps() {
  $("#selectedSteps").innerHTML = draftSteps.length
    ? draftSteps.map((step, index) => `<button type="button" class="selected-step ${index === selectedStepIndex ? "active" : ""}" data-index="${index}"><strong>${index + 1}. ${escapeHtml(step.name)}</strong><span>${typeText(jobPrimaryType(jobs.find((job) => job.id === step.config?.jobId)) || step.type)} · ${step.continueOnError ? "失败继续" : "失败停止"}</span></button>`).join("")
    : '<div class="empty-list">还没有子任务</div>';
  $$("#selectedSteps .selected-step").forEach((button) => button.addEventListener("click", () => { selectedStepIndex = Number(button.dataset.index); renderDraftSteps(); }));
}

function typeText(type) {
  return { import: "导入", export: "导出", query: "查询", job: "作业", sync: "同步" }[type] || type;
}

function openScheduleDialog(item = null) {
  const job = jobForSchedule(item);
  editingScheduleId = item?.id || "";
  editingJobId = job?.id || "";
  draftSteps = job ? JSON.parse(JSON.stringify(job.steps || [])) : [];
  selectedStepIndex = draftSteps.length ? 0 : -1;
  selectedAvailableJobId = "";
  draftRule = item?.rule || { mode: "interval", amount: 1, unit: "hours" };
  $("#scheduleDialogTitle").textContent = item ? "编辑任务" : "新增任务";
  $("#scheduleName").value = item?.name || "";
  $("#scheduleStart").value = normalizeDateTimeValue(item?.startAt, localDateValue(1));
  $("#scheduleEnd").value = normalizeDateTimeValue(item?.endAt, "2099-12-31T23:59:59");
  $("#keepLogs").checked = true;
  $("#logRetentionDays").value = item?.logRetentionDays || 3;
  $("#emailOnFail").checked = Boolean(item?.emailOnFail);
  renderStepConfig();
  renderDraftSteps();
  updateRuleSummary();
  $("#scheduleDialog").showModal();
}

function jobShareCount(jobId) {
  return schedules.filter((item) => item.jobId === jobId).length;
}

async function saveBackingJob() {
  if (!draftSteps.length) throw new Error("请至少添加一个子任务。");
  const currentSchedule = selectedSchedule();
  const canReuseJob = editingJobId && currentSchedule?.jobId === editingJobId && jobShareCount(editingJobId) <= 1;
  const payload = {
    id: canReuseJob ? editingJobId : "",
    name: `${$("#scheduleName").value || "未命名任务"} - 自动作业`,
    enabled: true,
    steps: draftSteps,
  };
  const saved = await requestJson("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  return saved.job;
}

async function saveSchedule() {
  const name = $("#scheduleName").value.trim();
  if (!name) throw new Error("请填写任务名称。");
  const job = await saveBackingJob();
  const payload = {
    id: editingScheduleId,
    name,
    jobId: job.id,
    enabled: true,
    startAt: $("#scheduleStart").value,
    endAt: $("#scheduleEnd").value,
    rule: draftRule,
    logRetentionDays: $("#keepLogs").checked ? Number($("#logRetentionDays").value || 3) : 9999,
    emailOnFail: false,
  };
  const saved = await requestJson("/api/schedules", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  selectedScheduleId = saved.schedule.id;
  $("#scheduleDialog").close();
  await Promise.all([loadJobs(), loadSchedules()]);
  setStatus(`定时任务已保存并启用，下次运行：${saved.schedule.nextRunAt || "未计算"}`, "success");
}

async function changeState(enabled) {
  if (!selectedScheduleId) throw new Error("请先选择定时任务。");
  await requestJson(`/api/schedules/${enabled ? "start" : "pause"}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: selectedScheduleId }) });
  await loadSchedules();
  setStatus(enabled ? "定时任务已启用。" : "定时任务已禁用。", "success");
}

async function runSelectedScheduleNow() {
  const item = selectedSchedule();
  if (!item) throw new Error("请先选择定时任务。");
  setStatus("正在立即运行...");
  const result = await requestJson("/api/jobs/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: item.jobId }) });
  await loadRuns();
  setStatus(`${result.run.status}：${result.run.message}`, result.run.status === "成功" ? "success" : "error");
}

async function loadRuns() {
  const item = selectedSchedule();
  if (!item) return;
  const payload = await requestJson(`/api/job-runs?jobId=${encodeURIComponent(item.jobId)}`);
  $("#scheduleRuns").innerHTML = (payload.runs || []).length
    ? payload.runs.map((run) => `<div class="log-item ${run.status === "成功" ? "success" : "failed"}"><strong>${escapeHtml(run.job_name)}<span>${escapeHtml(run.status)}</span></strong><div>${escapeHtml(run.started_at)} · ${escapeHtml(run.elapsed_ms)} ms</div><div>${escapeHtml(run.message)}</div>${(run.steps || []).map((step) => `<small>${step.step_index}. ${escapeHtml(step.step_name)} ${escapeHtml(step.status)} ${escapeHtml(step.message)}</small>`).join("")}</div>`).join("")
    : "暂无日志";
}

async function openLogDialog() {
  await loadRuns();
  $("#logDialog").showModal();
}

function openAssistant() {
  const isInterval = draftRule.mode === "interval";
  document.querySelector(`input[name="assistantMode"][value="${isInterval ? "interval" : "fixed"}"]`).checked = true;
  $("#assistantAmount").value = draftRule.amount || 1;
  $("#assistantUnit").value = draftRule.unit || "hours";
  $("#assistantFixedMode").value = ["daily", "weekly", "monthly", "yearly"].includes(draftRule.mode) ? draftRule.mode : "daily";
  $("#assistantTime").value = draftRule.time || "09:00:00";
  renderAssistantMode();
  $("#assistantDialog").showModal();
}

function renderAssistantMode() {
  const mode = document.querySelector('input[name="assistantMode"]:checked').value;
  $("#assistantIntervalPanel").classList.toggle("hidden", mode !== "interval");
  $("#assistantFixedPanel").classList.toggle("hidden", mode !== "fixed");
}

function applyAssistant() {
  const mode = document.querySelector('input[name="assistantMode"]:checked').value;
  if (mode === "interval") {
    draftRule = { mode: "interval", amount: Number($("#assistantAmount").value || 1), unit: $("#assistantUnit").value };
  } else {
    draftRule = { mode: $("#assistantFixedMode").value, time: $("#assistantTime").value };
    if (draftRule.mode === "weekly") draftRule.weekday = 1;
    if (draftRule.mode === "monthly") draftRule.day = 1;
    if (draftRule.mode === "yearly") {
      draftRule.month = 1;
      draftRule.day = 1;
    }
  }
  updateRuleSummary();
  $("#assistantDialog").close();
}

$("#newSchedule").addEventListener("click", () => openScheduleDialog());
$("#editSchedule").addEventListener("click", () => { const item = selectedSchedule(); if (item) openScheduleDialog(item); });
$("#deleteSchedule").addEventListener("click", async () => { if (!selectedScheduleId || !confirm("确认删除当前定时任务？")) return; await requestJson(`/api/schedules?id=${encodeURIComponent(selectedScheduleId)}`, { method: "DELETE" }); selectedScheduleId = ""; await loadSchedules(); });
$("#startSchedule").addEventListener("click", () => changeState(true).catch((error) => setStatus(error.message, "error")));
$("#pauseSchedule").addEventListener("click", () => changeState(false).catch((error) => setStatus(error.message, "error")));
$("#runScheduleNow").addEventListener("click", () => runSelectedScheduleNow().catch((error) => setStatus(error.message, "error")));
$("#viewScheduleLog").addEventListener("click", () => openLogDialog().catch((error) => setStatus(error.message, "error")));
$("#refreshSchedules").addEventListener("click", () => refreshAll().catch((error) => setStatus(error.message, "error")));
$("#closeScheduleDialog").addEventListener("click", () => $("#scheduleDialog").close());
$("#cancelSchedule").addEventListener("click", () => $("#scheduleDialog").close());
$("#saveSchedule").addEventListener("click", () => saveSchedule().catch((error) => setStatus(error.message, "error")));
$("#addStep").addEventListener("click", () => { try { addSelectedAvailableJob(); } catch (error) { setStatus(error.message, "error"); } });
$("#removeStep").addEventListener("click", () => { if (selectedStepIndex >= 0) draftSteps.splice(selectedStepIndex, 1); selectedStepIndex = Math.min(selectedStepIndex, draftSteps.length - 1); renderDraftSteps(); });
$("#moveStepUp").addEventListener("click", () => { if (selectedStepIndex > 0) { [draftSteps[selectedStepIndex - 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex - 1]]; selectedStepIndex -= 1; renderDraftSteps(); } });
$("#moveStepDown").addEventListener("click", () => { if (selectedStepIndex >= 0 && selectedStepIndex < draftSteps.length - 1) { [draftSteps[selectedStepIndex + 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex + 1]]; selectedStepIndex += 1; renderDraftSteps(); } });
$$('input[name="stepType"]').forEach((item) => item.addEventListener("change", () => { selectedAvailableJobId = ""; renderStepConfig(); }));
$("#openScheduleAssistant").addEventListener("click", openAssistant);
$("#closeAssistant").addEventListener("click", () => $("#assistantDialog").close());
$("#cancelAssistant").addEventListener("click", () => $("#assistantDialog").close());
$("#applyAssistant").addEventListener("click", applyAssistant);
$$('input[name="assistantMode"]').forEach((item) => item.addEventListener("change", renderAssistantMode));
$("#closeLogDialog").addEventListener("click", () => $("#logDialog").close());
$("#closeLogFooter").addEventListener("click", () => $("#logDialog").close());

refreshAll().catch((error) => setStatus(error.message, "error"));
setInterval(() => loadSchedules().catch(() => {}), 10000);
