const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let jobs = [];
let connections = [];
let selectedJobId = "";
let editingJobId = "";
let draftSteps = [];
let selectedStepIndex = -1;
let draftSourceJobId = "";

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

function renderRunLog(run) {
  const steps = run.steps || [];
  const successCount = steps.filter((step) => step.status === "成功").length;
  const failedCount = steps.filter((step) => step.status === "失败").length;
  return `<div class="log-item ${run.status === "成功" ? "success" : "failed"}">
    <strong>${escapeHtml(run.job_name)}<span>${escapeHtml(run.status)}</span></strong>
    <div class="run-meta-grid"><span><b>开始</b>${escapeHtml(run.started_at)}</span><span><b>结束</b>${escapeHtml(run.ended_at || "未结束")}</span><span><b>耗时</b>${escapeHtml(run.elapsed_ms)} ms</span><span><b>步骤</b>成功 ${successCount} / 失败 ${failedCount}</span></div>
    <div class="run-message">${escapeHtml(run.message)}</div>
    ${steps.map((step) => `<div class="run-step ${step.status === "成功" ? "success" : "failed"}">
      <strong>步骤 ${step.step_index}：${escapeHtml(step.step_name)}<span>${escapeHtml(step.status)}</span></strong>
      <div class="run-meta-grid step-meta"><span><b>类型</b>${escapeHtml(step.step_type)}</span><span><b>开始</b>${escapeHtml(step.started_at)}</span><span><b>结束</b>${escapeHtml(step.ended_at || "未结束")}</span><span><b>耗时</b>${escapeHtml(step.elapsed_ms)} ms</span></div>
      <div class="run-message">${escapeHtml(step.message || "无执行信息")}</div>
    </div>`).join("")}
  </div>`;
}

async function loadConnections() {
  const payload = await requestJson("/api/connections");
  connections = payload.connections || [];
}

// 判定是否"单步导入/导出任务"（作业可复用的资产，也是任务面板显示的实体）
function isTaskAsset(job) {
  const steps = job.steps || [];
  return steps.length === 1 && (steps[0].type === "import" || steps[0].type === "export");
}

// 判定是否定时任务自动生成的载体作业（名字以 " - 自动作业" 结尾，仅在调度内部使用）
function isScheduleBackingJob(job) {
  return String(job?.name || "").endsWith(" - 自动作业");
}

// 作业列表 = 全部 jobs 中剔除任务资产与自动作业载体，只留真正的作业
async function loadJobs() {
  const payload = await requestJson("/api/jobs");
  jobs = (payload.jobs || []).filter((job) => !isTaskAsset(job) && !isScheduleBackingJob(job));
  renderJobs();
  if (selectedJobId && !jobs.some((item) => item.id === selectedJobId)) selectedJobId = "";
  if (!selectedJobId && jobs[0]) selectedJobId = jobs[0].id;
  renderSelectedJob();
}

async function loadRuns() {
  const query = selectedJobId ? `?jobId=${encodeURIComponent(selectedJobId)}` : "";
  const payload = await requestJson(`/api/job-runs${query}`);
  $("#jobRuns").innerHTML = (payload.runs || []).length
    ? payload.runs.map(renderRunLog).join("")
    : "暂无日志";
}

function renderJobs() {
  $("#jobList").innerHTML = jobs.length
    ? jobs.map((job) => `<button class="job-item ${job.id === selectedJobId ? "active" : ""}" data-id="${escapeHtml(job.id)}"><strong>${escapeHtml(job.name)}</strong><span>${job.steps.length} 个步骤 · ${escapeHtml(job.updatedAt)}</span></button>`).join("")
    : '<div class="empty-list">暂无作业</div>';
  $$("#jobList .job-item").forEach((button) =>
    button.addEventListener("click", () => {
      selectedJobId = button.dataset.id;
      updateJobSelection();
      renderSelectedJob();
    }),
  );
  $$("#jobList .job-item").forEach((button) =>
    button.addEventListener("dblclick", () => {
      selectedJobId = button.dataset.id;
      const job = jobs.find((item) => item.id === selectedJobId);
      if (job) openJobDialog(job);
    }),
  );
  updateJobSelection();
}

function updateJobSelection() {
  $$("#jobList .job-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.id === selectedJobId);
  });
}

function renderSelectedJob() {
  const job = jobs.find((item) => item.id === selectedJobId);
  $("#stepPreview").innerHTML = job
    ? job.steps.map((step, index) => `<div class="step-row"><strong>${index + 1}. ${escapeHtml(step.name)}</strong><span>${escapeHtml(step.type)} · ${step.enabled ? "启用" : "禁用"} · ${step.continueOnError ? "失败继续" : "失败停止"}</span></div>`).join("")
    : "请选择作业";
  loadRuns().catch((error) => setStatus(error.message, "error"));
}

// ===== 作业编辑对话框：极简化 —— 添加步骤 = 从已保存任务中选择（复制其完整配置快照） =====

let taskOptions = []; // [{id, name, type, config}] 来自单步导入/导出任务

async function loadTaskOptions() {
  const payload = await requestJson("/api/jobs");
  const all = payload.jobs || [];
  taskOptions = all
    .filter((job) => isTaskAsset(job) && !isScheduleBackingJob(job) && job.id !== editingJobId)
    .map((job) => ({ id: job.id, name: job.name, type: job.steps[0].type, config: JSON.parse(JSON.stringify(job.steps[0].config || {})) }));
}

function taskTypeLabel(type) {
  return type === "import" ? "导入" : type === "export" ? "导出" : type;
}

function renderTaskOptions() {
  // 改为两个下拉：类型(导入/导出) + 任务（按类型筛选）
  const type = $("#addStepType")?.value || "import";
  const filtered = taskOptions.filter((t) => t.type === type);
  const taskSelect = $("#addStepTask");
  if (taskSelect) {
    taskSelect.innerHTML = filtered.length
      ? filtered.map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name)}</option>`).join("")
      : '<option value="">(暂无可用任务)</option>';
    taskSelect.disabled = filtered.length === 0;
  }
}

function draftStepFromSelection() {
  const sourceId = ($("#addStepTask")?.value || "").trim();
  const source = taskOptions.find((t) => t.id === sourceId);
  if (!source) throw new Error("请先在上方下拉中选择要执行的任务。");
  // 复制任务完整配置作为快照（v2 决策：作业自包含、与任务互不关联；复制后手动微调不再影响原任务）
  const stepType = source.type;
  const config = JSON.parse(JSON.stringify(source.config));
  if (stepType === "import") {
    // 仅保留执行所需键；路径/目标库/表来自任务快照
    config.importMode = config.importMode || "append";
  }
  return {
    id: crypto.randomUUID(),
    name: source.name,
    type: stepType,
    enabled: true,
    continueOnError: false,
    config,
  };
}

function renderDraftSteps() {
  $("#selectedSteps").innerHTML = draftSteps.length
    ? draftSteps.map((step, index) => `<button type="button" class="selected-step ${index === selectedStepIndex ? "active" : ""}" data-index="${index}"><strong>${index + 1}. ${escapeHtml(step.name)}</strong><span>${taskTypeLabel(step.type)} · ${step.continueOnError ? "失败继续" : "失败停止"}</span></button>`).join("")
    : '<div class="empty-list">还没有子任务</div>';
  $$("#selectedSteps .selected-step").forEach((button) => button.addEventListener("click", () => { selectedStepIndex = Number(button.dataset.index); renderDraftSteps(); }));
}

async function openJobDialog(job = null) {
  editingJobId = job?.id || "";
  $("#addStepType").value = "import";
  $("#jobDialogTitle").textContent = job ? "编辑作业" : "新增作业";
  $("#jobName").value = job?.name || "";
  draftSteps = job ? JSON.parse(JSON.stringify(job.steps)) : [];
  selectedStepIndex = draftSteps.length ? 0 : -1;
  try {
    await loadTaskOptions();
  } catch (error) {
    setStatus(error.message, "error");
  }
  renderTaskOptions();
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

async function duplicateSelectedJob() {
  const job = jobs.find((item) => item.id === selectedJobId);
  if (!job) throw new Error("请先选择一个要复制的作业。");
  const stepsCopy = JSON.parse(JSON.stringify(job.steps));
  const payload = { id: "", name: `${job.name}（副本）`, enabled: true, steps: stepsCopy };
  const saved = await requestJson("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  selectedJobId = saved.job.id;
  await loadJobs();
  setStatus(`已复制为「${saved.job.name}」，可选中后编辑修改。`, "success");
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
$("#duplicateJob").addEventListener("click", () => duplicateSelectedJob().catch((error) => setStatus(error.message, "error")));
$("#deleteJob").addEventListener("click", async () => { if (!selectedJobId || !confirm("确认删除当前作业？")) return; await requestJson(`/api/jobs?id=${encodeURIComponent(selectedJobId)}`, { method: "DELETE" }); selectedJobId = ""; await loadJobs(); });
$("#runJob").addEventListener("click", () => runSelectedJob().catch((error) => setStatus(error.message, "error")));
$("#refreshJobs").addEventListener("click", () => Promise.all([loadJobs(), loadRuns()]));
$("#closeJobDialog").addEventListener("click", () => $("#jobDialog").close());
$("#cancelJob").addEventListener("click", () => $("#jobDialog").close());
$("#saveJob").addEventListener("click", () => saveJob().catch((error) => setStatus(error.message, "error")));
$("#addStep").addEventListener("click", () => { try { draftSteps.push(draftStepFromSelection()); selectedStepIndex = draftSteps.length - 1; renderDraftSteps(); } catch (error) { setStatus(error.message, "error"); } });
$("#addStepType").addEventListener("change", renderTaskOptions);
$("#removeStep").addEventListener("click", () => { if (selectedStepIndex >= 0) draftSteps.splice(selectedStepIndex, 1); selectedStepIndex = Math.min(selectedStepIndex, draftSteps.length - 1); renderDraftSteps(); });
$("#moveStepUp").addEventListener("click", () => { if (selectedStepIndex > 0) { [draftSteps[selectedStepIndex - 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex - 1]]; selectedStepIndex -= 1; renderDraftSteps(); } });
$("#moveStepDown").addEventListener("click", () => { if (selectedStepIndex >= 0 && selectedStepIndex < draftSteps.length - 1) { [draftSteps[selectedStepIndex + 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex + 1]]; selectedStepIndex += 1; renderDraftSteps(); } });

Promise.all([loadConnections(), loadJobs()]).catch((error) => setStatus(error.message, "error"));
