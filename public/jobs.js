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
  // 运行结果里可能带多行产物路径（每个文件一行），用 textContent 会把换行压成空白，
  // 用户看不到文件落在哪；这里转义后用 <br> 保留换行。
  const text = String(message ?? "");
  $("#jobStatus").innerHTML = escapeHtml(text).replace(/\r?\n/g, "<br>");
  $("#jobStatus").className = type;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

// 状态条只留结论句：run.message 里的「产出文件（N 个）：」表头 + 逐行绝对路径 + 默认目录警告，
// 是给下方运行记录看的明细（日志侧仍由 runMessageText() 从 run.outputs 重建），塞进状态条
// 会把一行结论撑成多行。这里只把明细块剥掉，结论句与「最后错误：…」一定留在状态条上。
//
// 判定依据（刻意不依赖"像路径就当产物"的宽正则，那会把导入失败错误里内联的
// `D:\input\不存在的源表.xlsx 路径不存在` 误删——上一轮点名的 P2-B）：
//   1) 「产出文件…」表头行必丢，并从表头里读出它声明的条数 N，记下"后面还有 N 行明细"；
//   2) 明细块内的每一行，只有**命中 run.outputs 白名单**（经 outputPathKey 归一化后比对）
//      才丢；命中不了就说明它不属于本次产物 → 立刻结束明细块，这一行照常保留；
//   3) 历史记录没有 outputs 字段（白名单为空）时退化为"按表头声明的 N 行丢"，且 N 用尽即停，
//      所以最多只丢表头自己承认的那几行，不会越过明细块去吞结论句或错误文本；
//   4) 「（警告：…）」行一律丢（警告含义已写进日志侧，状态条不需要）。
function statusSummary(run) {
  const source = String(run?.message ?? "");
  if (!source.trim()) return source;
  const knownKeys = new Set((Array.isArray(run?.outputs) ? run.outputs : []).map(outputPathKey).filter(Boolean));
  const kept = [];
  let pendingPaths = 0; // 明细块内还需要丢掉的产物行数（来自表头声明的 N）
  for (const line of source.split(/\r?\n/).map((item) => item.trim())) {
    if (line.startsWith("产出文件")) {
      const declared = /（\s*(\d+)\s*个/.exec(line);
      pendingPaths = declared ? Number(declared[1]) : 0;
      continue;
    }
    if (line.startsWith("（警告：")) continue;
    if (pendingPaths > 0) {
      if (!line) continue; // 明细块里的空行只当分隔，不消耗 N
      if (knownKeys.size && !knownKeys.has(outputPathKey(line))) {
        pendingPaths = 0; // 这一行不在产物白名单里，明细块到此结束，该行按内容保留
      } else {
        pendingPaths -= 1;
        continue;
      }
    }
    kept.push(line);
  }
  const summary = kept.join(" ").replace(/\s+/g, " ").trim();
  if (summary) return summary;
  // 极端情况（整条 message 只有明细块、没有结论句）：宁可原样显示，也别给用户一片空白
  return source.replace(/\s+/g, " ").trim() || source;
}

function runStateClass(status) {
  if (status === "成功") return "success";
  if (status === "跳过") return "skipped";
  return "failed";
}

// 产物路径的去重键（P2-A）：Windows 路径大小写不敏感、\ 与 / 是同一分隔符，同一个真实文件
// 的大小写变体必须算成 1 个；语义与服务端 server.py::output_dedupe_key 对齐，只用于比较，
// 渲染始终用首次出现的原始字符串。
function outputPathKey(path) {
  const text = String(path ?? "").trim();
  if (!text) return "";
  const windowsStyle = /^[A-Za-z]:/.test(text) || text.includes("\\");
  return windowsStyle ? text.replace(/\\/g, "/").toLowerCase() : text.replace(/\\/g, "/");
}

// 详情页的 run 文本：产物清单改读服务端 run.outputs 字段（P3-B），不再从 message 文本里正则
// 认路径（P2-B：像「D:\input\不存在的源表.xlsx 路径不存在」这样的行会被误当成产物）。
// message 里原有的清单行剔除后由这里重新生成唯一一次，表头与路径因此各只出现一次（P1-A）。
// 没有 outputs 字段的历史记录原样展示 message，不报错、不丢信息。
function runMessageText(run) {
  const structured = (Array.isArray(run.outputs) ? run.outputs : []).map((item) => String(item ?? "").trim()).filter(Boolean);
  const source = String(run.message ?? "");
  if (!structured.length) return source;
  const keys = new Set(structured.map(outputPathKey));
  const lines = source.split(/\r?\n/).map((line) => line.trim());
  const body = lines
    .filter((line) => line && !line.startsWith("产出文件") && !line.startsWith("（警告：") && !keys.has(outputPathKey(line)))
    .join(" ");
  const warnings = lines.filter((line) => line.startsWith("（警告："));
  const unique = [];
  const seen = new Set();
  structured.forEach((item) => {
    const key = outputPathKey(item);
    if (seen.has(key)) return;
    seen.add(key);
    unique.push(item);
  });
  const label = run.status === "成功" ? `产出文件（${unique.length} 个）：` : `产出文件（${unique.length} 个，失败前已落盘）：`;
  return [body, label, ...unique, ...warnings].filter(Boolean).join("\n");
}

function renderRunLog(run) {
  const steps = run.steps || [];
  const successCount = steps.filter((step) => step.status === "成功").length;
  const failedCount = steps.filter((step) => step.status === "失败").length;
  return `<div class="log-item ${runStateClass(run.status)}">
    <strong>${escapeHtml(run.job_name)}<span>${escapeHtml(run.status)}</span></strong>
    <div class="run-meta-grid"><span><b>开始</b>${escapeHtml(run.started_at)}</span><span><b>结束</b>${escapeHtml(run.ended_at || "未结束")}</span><span><b>耗时</b>${escapeHtml(run.elapsed_ms)} ms</span><span><b>步骤</b>成功 ${successCount} / 失败 ${failedCount}</span></div>
    <div class="run-message">${escapeHtml(runMessageText(run))}</div>
    ${steps.map((step) => `<div class="run-step ${runStateClass(step.status)}">
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

function guardSummary(job) {
  const g = (job && job.guard) || {};
  if (!g || !g.type) return "";
  if (g.type === "file_has_new") return "执行条件：源文件有更新才执行（自动检查，无需填写）";
  if (g.type === "date_match") {
    const mode = g.mode || "";
    if (mode === "range") return `执行条件：仅 ${g.start || "?"} ~ ${g.end || "?"} 期间执行`;
    if (mode === "dates") return `执行条件：仅指定日期执行（${(g.values || []).length} 天）`;
    if (mode === "weekday") {
      const names = { 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日" };
      return `执行条件：仅 ${(g.values || []).map((v) => names[v] || v).join("、")} 执行`;
    }
    if (mode === "monthday") return `执行条件：仅每月 ${(g.values || []).join("、")} 号执行`;
    return "执行条件：仅指定日期执行";
  }
  return "";
}

function renderSelectedJob() {
  const job = jobs.find((item) => item.id === selectedJobId);
  const gText = job ? guardSummary(job) : "";
  $("#stepPreview").innerHTML = (job
    ? (gText ? `<div class="step-row guard-summary"><strong>${escapeHtml(gText)}</strong></div>` : "") +
      job.steps.map((step, index) => `<div class="step-row"><strong>${index + 1}. ${escapeHtml(step.name)}</strong></div>`).join("")
    : "请选择作业");
  loadRuns().catch((error) => setStatus(error.message, "error"));
}

// ===== 作业编辑对话框：极简化 —— 添加步骤 = 从已保存任务中选择（复制其完整配置快照） =====

let taskOptions = []; // [{id, name, type, config}] 单步导入/导出任务 + 保存查询

async function loadTaskOptions() {
  const jobPayload = await requestJson("/api/jobs");
  const all = jobPayload.jobs || [];
  const importExport = all
    .filter((job) => isTaskAsset(job) && !isScheduleBackingJob(job) && job.id !== editingJobId)
    .map((job) => ({ id: job.id, name: job.name, type: job.steps[0].type, config: JSON.parse(JSON.stringify(job.steps[0].config || {})) }));
  // 查询类型：来自 _saved_queries 资产（执行时按 queryId 读取最新 SQL + 连接）
  let queryItems = [];
  try {
    const qPayload = await requestJson("/api/queries");
    queryItems = (qPayload.queries || []).map((q) => ({
      id: q.id,
      name: q.name,
      type: "query",
      config: { queryId: q.id, connectionId: q.connection_id || "" },
    }));
  } catch (error) {
    console.warn("加载保存查询失败（查询类型不可用）:", error.message);
  }
  taskOptions = [...importExport, ...queryItems];
}

function taskTypeLabel(type) {
  return type === "import" ? "导入" : type === "export" ? "导出" : type === "query" ? "查询" : type;
}

function renderTaskOptions() {
  // 两个下拉：类型(导入/导出/查询) + 任务（按类型筛选）
  const type = $("#addStepType")?.value || "import";
  const filtered = taskOptions.filter((t) => t.type === type);
  const taskSelect = $("#addStepTask");
  if (taskSelect) {
    taskSelect.innerHTML = filtered.length
      ? filtered.map((t) => `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name)}</option>`).join("")
      : '<option value="">(暂无可用任务)</option>';
    taskSelect.disabled = filtered.length === 0;
  }
  renderAddTaskHint();
}

// 导出任务的目标文件夹为空时，导出会静默回退到服务端默认目录，作业照样报"成功"
// 而用户以为"没输出"。选任务时就提示，避免这种配置漏填无人察觉。
function renderAddTaskHint() {
  const hint = $("#addTaskHint");
  if (!hint) return;
  const source = taskOptions.find((t) => t.id === ($("#addStepTask")?.value || "").trim());
  if (!source || source.type !== "export") {
    hint.textContent = "";
    hint.className = "hint";
    return;
  }
  const folder = String(source.config?.exportFolder || "").trim();
  if (!folder) {
    hint.textContent = "注意：该导出任务未设置目标文件夹，运行结果将落在服务端默认目录。";
    hint.className = "hint warn";
    return;
  }
  hint.textContent = `该导出任务的输出文件夹：${folder}`;
  hint.className = "hint";
}

function draftStepFromSelection() {
  const sourceId = ($("#addStepTask")?.value || "").trim();
  const source = taskOptions.find((t) => t.id === sourceId);
  if (!source) throw new Error("请先在上方下拉中选择要执行的任务。");
  const stepType = source.type;
  // 导入/导出：复制任务完整配置作为快照（v2 决策：作业自包含、与任务互不关联）
  // 查询：存 queryId 引用（保存查询为独立资产，执行时读取其最新 SQL + 连接）
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
    ? draftSteps.map((step, index) => `<button type="button" class="selected-step ${index === selectedStepIndex ? "active" : ""}" data-index="${index}"><strong>${index + 1}. ${escapeHtml(step.name)}</strong></button>`).join("")
    : '<div class="empty-list">还没有子任务</div>';
  $$("#selectedSteps .selected-step").forEach((button) => button.addEventListener("click", () => { selectedStepIndex = Number(button.dataset.index); renderDraftSteps(); }));
}

// ===== 作业级执行条件（文件有新增 / 日期守卫）=====
let guardDateValues = [];

function renderGuardConfig() {
  const type = $("#guardType")?.value || "";
  const f = $("#guardFileConfig");
  const d = $("#guardDateConfig");
  if (f) f.style.display = type === "file_has_new" ? "" : "none";
  if (d) d.style.display = type === "date_match" ? "" : "none";
  const mode = $("#guardDateMode")?.value || "range";
  const rows = {
    range: $("#guardRangeRow"),
    dates: $("#guardDatesRow"),
    weekday: $("#guardWeekdayRow"),
    monthday: $("#guardMonthdayRow"),
  };
  Object.entries(rows).forEach(([key, el]) => {
    if (el) el.style.display = mode === key ? "" : "none";
  });
}

function renderGuardDateList() {
  const list = $("#guardDatesList");
  if (!list) return;
  list.innerHTML = guardDateValues.length
    ? guardDateValues.map((date) => `<span class="date-chip">${escapeHtml(date)}<button type="button" data-date="${escapeHtml(date)}">×</button></span>`).join("")
    : '<span class="hint">尚未添加日期</span>';
  $$("#guardDatesList .date-chip button").forEach((btn) =>
    btn.addEventListener("click", () => {
      guardDateValues = guardDateValues.filter((d) => d !== btn.dataset.date);
      renderGuardDateList();
    }),
  );
}

function loadGuardFromJob(job) {
  const guard = (job && job.guard) || {};
  guardDateValues = [];
  const gtype = guard.type || "";
  $("#guardType").value = gtype;
  if (gtype === "date_match") {
    const mode = guard.mode || "range";
    $("#guardDateMode").value = mode;
    if (mode === "range") {
      $("#guardRangeStart").value = guard.start || "";
      $("#guardRangeEnd").value = guard.end || "";
    } else if (mode === "dates") {
      guardDateValues = Array.isArray(guard.values) ? guard.values.map((v) => String(v)) : [];
    } else if (mode === "weekday") {
      const values = Array.isArray(guard.values) ? guard.values.map((v) => String(v)) : [];
      $$(".guard-weekday").forEach((box) => { box.checked = values.includes(box.value); });
    } else if (mode === "monthday") {
      $("#guardMonthday").value = Array.isArray(guard.values) && guard.values.length ? guard.values[0] : "";
    }
  }
  renderGuardDateList();
  renderGuardConfig();
}

function collectGuard() {
  const type = $("#guardType")?.value || "";
  if (!type) return {};
  if (type === "file_has_new") return { type };
  if (type === "date_match") {
    const mode = $("#guardDateMode").value;
    if (mode === "range") {
      const start = ($("#guardRangeStart").value || "").trim();
      const end = ($("#guardRangeEnd").value || "").trim();
      if (!start || !end) throw new Error("请填写日期范围（开始与结束日期）。");
      if (start > end) throw new Error("开始日期不能晚于结束日期。");
      return { type, mode, start, end };
    }
    if (mode === "dates") {
      if (!guardDateValues.length) throw new Error("请至少添加一个执行日期。");
      return { type, mode, values: [...guardDateValues].sort() };
    }
    if (mode === "weekday") {
      const values = $$(".guard-weekday:checked").map((box) => Number(box.value));
      if (!values.length) throw new Error("请选择至少一个星期。");
      return { type, mode, values };
    }
    const day = Number($("#guardMonthday").value);
    if (!(day >= 1 && day <= 31)) throw new Error("请填写有效的每月几号（1-31）。");
    return { type, mode, values: [day] };
  }
  return {};
}

async function openJobDialog(job = null) {
  editingJobId = job?.id || "";
  $("#addStepType").value = "import";
  $("#jobDialogTitle").textContent = job ? "编辑作业" : "新增作业";
  $("#jobName").value = job?.name || "";
  draftSteps = job ? JSON.parse(JSON.stringify(job.steps)) : [];
  selectedStepIndex = draftSteps.length ? 0 : -1;
  loadGuardFromJob(job);
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
  const guard = collectGuard();
  const payload = { id: editingJobId, name: $("#jobName").value, enabled: true, steps: draftSteps, guard };
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
  // 状态条只给结论句，产物清单留给下方运行记录（见 statusSummary 注释）
  setStatus(`${payload.run.status}：${statusSummary(payload.run)}`, payload.run.status === "成功" ? "success" : payload.run.status === "跳过" ? "skipped" : "error");
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
$("#addStepTask").addEventListener("change", renderAddTaskHint);
$("#guardType").addEventListener("change", renderGuardConfig);
$("#guardDateMode").addEventListener("change", renderGuardConfig);
$("#guardAddDate").addEventListener("click", () => {
  const picker = $("#guardDatePicker");
  const value = (picker && picker.value) || "";
  if (!value) { setStatus("请先选择要添加的日期。", "error"); return; }
  if (!guardDateValues.includes(value)) guardDateValues.push(value);
  renderGuardDateList();
});
$("#removeStep").addEventListener("click", () => { if (selectedStepIndex >= 0) draftSteps.splice(selectedStepIndex, 1); selectedStepIndex = Math.min(selectedStepIndex, draftSteps.length - 1); renderDraftSteps(); });
$("#moveStepUp").addEventListener("click", () => { if (selectedStepIndex > 0) { [draftSteps[selectedStepIndex - 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex - 1]]; selectedStepIndex -= 1; renderDraftSteps(); } });
$("#moveStepDown").addEventListener("click", () => { if (selectedStepIndex >= 0 && selectedStepIndex < draftSteps.length - 1) { [draftSteps[selectedStepIndex + 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex + 1]]; selectedStepIndex += 1; renderDraftSteps(); } });

Promise.all([loadConnections(), loadJobs()]).catch((error) => setStatus(error.message, "error"));
