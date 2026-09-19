const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let jobs = [];
let schedules = [];
let savedQueries = []; // 查询页保存的查询资产，定时子任务「查询」类型的候选来源
let selectedScheduleId = "";
let editingScheduleId = "";
let editingJobId = "";
let draftSteps = [];
let selectedStepIndex = -1;
let selectedAvailableJobId = "";
let draftRule = { mode: "interval", amount: 1, unit: "hours" };
let scheduleRefreshRunning = false;

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
  // 运行结果里可能带多行产物路径（每个文件一行），用 textContent 会把换行压成空白，
  // 用户看不到文件落在哪；这里转义后用 <br> 保留换行。
  const text = String(message ?? "");
  $("#scheduleStatus").innerHTML = escapeHtml(text).replace(/\r?\n/g, "<br>");
  $("#scheduleStatus").className = type;
}

// 状态条只留结论句：run.message 里的「产出文件（N 个）：」表头 + 逐行绝对路径 + 默认目录警告，
// 是给「查看日志」看的明细（日志侧仍由 mergeScheduleRuns() 从 run.outputs 重建），塞进状态条
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
  const refreshTime = $("#lastRefreshTime");
  if (refreshTime) refreshTime.textContent = `刷新时间 · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
}

async function loadSavedQueries() {
  try {
    const payload = await requestJson("/api/queries");
    savedQueries = payload.queries || [];
  } catch (error) {
    console.warn("加载保存查询失败（查询类型不可用）:", error.message);
    savedQueries = [];
  }
}

async function refreshAll() {
  await Promise.all([loadJobs(), loadSchedules(), loadSavedQueries()]);
}

async function refreshSchedulesLive(includeAll = false) {
  if (scheduleRefreshRunning || document.hidden) return;
  scheduleRefreshRunning = true;
  const button = $("#refreshSchedules");
  button?.classList.add("refreshing");
  try {
    if (includeAll) await refreshAll();
    else await loadSchedules();
    if ($("#logDialog")?.open) await loadRuns();
  } finally {
    scheduleRefreshRunning = false;
    button?.classList.remove("refreshing");
  }
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
  if (!item?.lastRunAt) return "尚未执行";
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
            <td>${escapeHtml(item.lastRunAt || "尚未执行")}</td>
            <td>${escapeHtml(lastResultText(item))}</td>
          </tr>`,
        )
        .join("")
    : '<tr><td colspan="6" class="schedule-empty">暂无定时任务</td></tr>';

  $$("#scheduleTableBody tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => {
      selectedScheduleId = row.dataset.id;
      renderSchedules();
    });
    row.addEventListener("dblclick", () => {
      selectedScheduleId = row.dataset.id;
      const item = selectedSchedule();
      if (item) openScheduleDialog(item);
    });
  });
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

function typeText(type) {
  return { import: "导入", export: "导出", query: "查询", job: "作业", sync: "同步" }[type] || type;
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
    // 严格按主类型过滤：选「作业」只列真正的作业（多步骤/含子作业），
    // 不再把导入/导出等单步任务混进作业候选区。
    return jobPrimaryType(job) === type;
  });
}

function renderStepConfig() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  // 候选 = 作业（按主类型过滤）+ 保存的查询（仅「查询」类型，来自 _saved_queries 资产）
  const candidates = candidateJobsForType(type).map((job) => ({ kind: "job", id: job.id, name: job.name }));
  if (type === "query") {
    candidates.push(...savedQueries.map((q) => ({ kind: "query", id: q.id, name: q.name })));
  }
  if (!candidates.some((item) => item.id === selectedAvailableJobId)) {
    selectedAvailableJobId = candidates[0]?.id || "";
  }
  $("#stepConfig").innerHTML = candidates.length
    ? candidates
        .map((item) => `<button type="button" class="available-job ${item.id === selectedAvailableJobId ? "active" : ""}" data-id="${escapeHtml(item.id)}"><strong>${escapeHtml(item.name)}</strong></button>`)
        .join("")
    : type === "query"
      ? '<div class="empty-list">暂无可选查询，请先在查询页面点击「保存查询」</div>'
      : `<div class="empty-list">暂无${typeText(type)}作业，请先在${typeText(type)}页面保存作业</div>`;
  $$("#stepConfig .available-job").forEach((button) =>
    button.addEventListener("click", () => {
      selectedAvailableJobId = button.dataset.id;
      renderStepConfig();
    }),
  );
}

function addSelectedAvailableJob() {
  const type = document.querySelector('input[name="stepType"]:checked').value;
  if (type === "sync") throw new Error("同步模块尚未开放。");
  // 查询类型：候选来自保存的查询资产，步骤按 queryId 引用（执行时读取最新 SQL + 连接）
  const query = savedQueries.find((q) => q.id === selectedAvailableJobId);
  if (type === "query" || query) {
    if (!query) throw new Error("请先选择一个保存的查询。");
    draftSteps.push({
      id: crypto.randomUUID(),
      type: "query",
      name: query.name,
      enabled: true,
      continueOnError: false,
      config: { queryId: query.id, connectionId: query.connectionId || query.connection_id || "" },
    });
  } else {
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
  }
  selectedStepIndex = draftSteps.length - 1;
  renderDraftSteps();
}

function renderDraftSteps() {
  $("#selectedSteps").innerHTML = draftSteps.length
    ? draftSteps
        .map((step, index) => `<button type="button" class="selected-step ${index === selectedStepIndex ? "active" : ""}" data-index="${index}"><strong>${index + 1}. ${escapeHtml(step.name)}</strong><span>${step.continueOnError ? "失败继续" : "失败停止"}</span></button>`)
        .join("")
    : '<div class="empty-list">还没有子任务</div>';
  $$("#selectedSteps .selected-step").forEach((button) =>
    button.addEventListener("click", () => {
      selectedStepIndex = Number(button.dataset.index);
      renderDraftSteps();
    }),
  );
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
  const result = await requestJson("/api/jobs/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: item.jobId, scheduleId: item.id }) });
  await loadRuns();
  await loadSchedules();
  // 状态条只给结论句，产物清单留给下方「查看日志」（见 statusSummary 注释）
  setStatus(`${result.run.status}：${statusSummary(result.run)}`, result.run.status === "成功" ? "success" : "error");
}

async function loadRuns() {
  const payload = await requestJson("/api/job-runs");
  const allRuns = payload.runs || [];
  const scheduleMap = new Map(schedules.map((item) => [item.id, item]));
  const mergedRuns = [];
  for (const item of schedules) {
    const related = allRuns.filter((run) => run.schedule_id === item.id);
    mergedRuns.push(...mergeScheduleRuns(related, item.jobId));
  }
  const orphanRuns = allRuns.filter((run) => run.schedule_id && !scheduleMap.has(run.schedule_id));
  mergedRuns.push(...orphanRuns);
  mergedRuns.sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
  $("#scheduleRuns").innerHTML = mergedRuns.length
    ? mergedRuns.map(renderRunLog).join("")
    : `<div class="schedule-no-runs"><strong>暂无定时任务运行日志</strong><span>任务执行后会在这里统一显示成功或失败信息。</span></div>`;
}

// 产物路径的去重键（P2-A）：Windows 路径大小写不敏感、\ 与 / 是同一分隔符，同一个真实
// 文件写成 ...\out\x.csv 与 ...\OUT\x.csv 时必须算成 1 个。语义与服务端
// server.py::output_dedupe_key（os.path.normcase + normpath）保持一致；展示用的是首次出现
// 的原始字符串，本函数只用于比较，绝不参与渲染。
function outputPathKey(path) {
  const text = String(path ?? "").trim();
  if (!text) return "";
  const windowsStyle = /^[A-Za-z]:/.test(text) || text.includes("\\");
  const unified = text.replace(/\\/g, "/");
  return windowsStyle ? unified.toLowerCase() : unified;
}

function mergeScheduleRuns(runs, rootJobId) {
  const groups = new Map();
  for (const run of runs) {
    const key = `${run.schedule_id || "manual"}|${run.started_at}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run);
  }

  const merged = [];
  for (const group of groups.values()) {
    const root = group.find((run) => run.job_id === rootJobId) || group[0];
    const related = group.filter((run) => run !== root);
    if (!related.length) {
      merged.push(root);
      continue;
    }

    const childSteps = related.flatMap((child) =>
      (child.steps || []).map((step) => ({
        ...step,
        step_name: `${child.job_name} / ${step.step_name}`,
      })),
    );
    const directSteps = (root.steps || []).filter((step) => step.step_type !== "job");
    const allRuns = [root, ...related];
    const failedRuns = allRuns.filter((run) => run.status !== "成功");
    const endedTimes = allRuns.map((run) => run.ended_at).filter(Boolean).sort();
    // 产物清单优先读服务端 run.outputs 字段（P3-B）：message 文本只是给人看的说明书，
    // 从文本里正则认路径会把"像路径的行"（如导入失败错误里内联的 D:\input\x.xlsx 路径不存在）
    // 误当成产物并计入 N（P2-B）。只有历史记录（本次改动前落库、没有该字段）才回退到
    // 解析 message 的「产出文件」块，保证老日志仍能正常渲染而不是报错。
    const outputFiles = [];
    const outputKeys = new Set();
    const warningLines = [];
    const addOutput = (item) => {
      const text = String(item ?? "").trim();
      const key = outputPathKey(text);
      if (!text || !key || outputKeys.has(key)) return;
      outputKeys.add(key);
      outputFiles.push(text);
    };
    const hasStructuredOutputs = (run) => Array.isArray(run.outputs) && run.outputs.length > 0;
    for (const run of allRuns) {
      const messageLines = String(run.message || "").split(/\r?\n/).map((line) => line.trim());
      // 默认目录警告是给用户看的提示，与产物清单来源无关，任何 run 都要收集（否则会漏提示）
      for (const line of messageLines) {
        if (line.startsWith("（警告：") && !warningLines.includes(line)) warningLines.push(line);
      }
      if (hasStructuredOutputs(run)) {
        run.outputs.forEach(addOutput);
        continue;
      }
      // 降级路径：老记录没有 outputs 字段，只能在 message 里找，但只认「产出文件」块内的路径行
      let inBlock = false;
      for (const line of messageLines) {
        if (line.startsWith("产出文件")) {
          inBlock = true;
          continue;
        }
        if (inBlock && line && /^[A-Za-z]:[\\/]/.test(line)) {
          addOutput(line);
          continue;
        }
        inBlock = false;
      }
    }
    // 汇总 / 失败文本要剔除清单行，否则会与下面重新生成的清单一并显示（表头与路径各出现两遍）。
    // structured 为真时只按"确属本次产物"的白名单剔除，不动错误文本里的其他路径行。
    const dropDetailLines = (text, structured) =>
      String(text || "")
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => {
          if (!line) return false;
          if (line.startsWith("产出文件") || line.startsWith("（警告：")) return false;
          if (outputKeys.has(outputPathKey(line))) return false;
          if (!structured && /^[A-Za-z]:[\\/]/.test(line)) return false;
          return true;
        })
        .join(" ");
    const detailLines = [...(outputFiles.length ? [`产出文件（${outputFiles.length} 个）：`, ...outputFiles] : []), ...warningLines];
    const detailText = detailLines.length ? `\n${detailLines.join("\n")}` : "";
    merged.push({
      ...root,
      ended_at: endedTimes.at(-1) || root.ended_at,
      elapsed_ms: Math.max(...allRuns.map((run) => Number(run.elapsed_ms || 0))),
      status: failedRuns.length ? "失败" : "成功",
      outputs: outputFiles,
      message: failedRuns.length
        ? `本次任务执行失败：${failedRuns.map((run) => `${run.job_name}：${dropDetailLines(run.message, hasStructuredOutputs(run))}`).join("；")}${detailText}`
        : `本次任务执行成功，共完成 ${directSteps.length + childSteps.length} 个实际步骤。${detailText}`,
      steps: [...directSteps, ...childSteps].map((step, index) => ({ ...step, step_index: index + 1 })),
    });
  }
  return merged.sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
}

function renderRunLog(run) {
  const steps = run.steps || [];
  const successCount = steps.filter((step) => step.status === "成功").length;
  const failedCount = steps.filter((step) => step.status === "失败").length;
  return `<div class="log-item ${run.status === "成功" ? "success" : "failed"}">
    <strong>${escapeHtml(run.job_name)}<span>${escapeHtml(run.status)}</span></strong>
    <div class="run-meta-grid"><span><b>开始</b>${escapeHtml(run.started_at)}</span><span><b>结束</b>${escapeHtml(run.ended_at || "未结束")}</span><span><b>耗时</b>${escapeHtml(run.elapsed_ms ?? "")} ms</span><span><b>步骤</b>成功 ${successCount} / 失败 ${failedCount}</span></div>
    <div class="run-message">${escapeHtml(run.message)}</div>
    ${steps.map((step) => `<div class="run-step ${step.status === "成功" ? "success" : "failed"}">
      <strong>步骤 ${step.step_index}：${escapeHtml(step.step_name)}<span>${escapeHtml(step.status)}</span></strong>
      <div class="run-meta-grid step-meta"><span><b>类型</b>${escapeHtml(step.step_type)}</span><span><b>开始</b>${escapeHtml(step.started_at)}</span><span><b>结束</b>${escapeHtml(step.ended_at || "未结束")}</span><span><b>耗时</b>${escapeHtml(step.elapsed_ms)} ms</span></div>
      <div class="run-message">${escapeHtml(step.message || "无执行信息")}</div>
    </div>`).join("")}
  </div>`;
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
$("#editSchedule").addEventListener("click", () => {
  const item = selectedSchedule();
  if (item) openScheduleDialog(item);
});
$("#deleteSchedule").addEventListener("click", async () => {
  if (!selectedScheduleId || !confirm("确认删除当前定时任务？")) return;
  await requestJson(`/api/schedules?id=${encodeURIComponent(selectedScheduleId)}`, { method: "DELETE" });
  selectedScheduleId = "";
  await loadSchedules();
});
$("#startSchedule").addEventListener("click", () => changeState(true).catch((error) => setStatus(error.message, "error")));
$("#pauseSchedule").addEventListener("click", () => changeState(false).catch((error) => setStatus(error.message, "error")));
$("#runScheduleNow").addEventListener("click", () => runSelectedScheduleNow().catch((error) => setStatus(error.message, "error")));
$("#viewScheduleLog").addEventListener("click", () => openLogDialog().catch((error) => setStatus(error.message, "error")));
$("#refreshSchedules").addEventListener("click", () => refreshSchedulesLive(true).catch((error) => setStatus(error.message, "error")));
$("#closeScheduleDialog").addEventListener("click", () => $("#scheduleDialog").close());
$("#cancelSchedule").addEventListener("click", () => $("#scheduleDialog").close());
$("#saveSchedule").addEventListener("click", () => saveSchedule().catch((error) => setStatus(error.message, "error")));
$("#addStep").addEventListener("click", () => {
  try {
    addSelectedAvailableJob();
  } catch (error) {
    setStatus(error.message, "error");
  }
});
$("#removeStep").addEventListener("click", () => {
  if (selectedStepIndex >= 0) draftSteps.splice(selectedStepIndex, 1);
  selectedStepIndex = Math.min(selectedStepIndex, draftSteps.length - 1);
  renderDraftSteps();
});
$("#moveStepUp").addEventListener("click", () => {
  if (selectedStepIndex > 0) {
    [draftSteps[selectedStepIndex - 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex - 1]];
    selectedStepIndex -= 1;
    renderDraftSteps();
  }
});
$("#moveStepDown").addEventListener("click", () => {
  if (selectedStepIndex >= 0 && selectedStepIndex < draftSteps.length - 1) {
    [draftSteps[selectedStepIndex + 1], draftSteps[selectedStepIndex]] = [draftSteps[selectedStepIndex], draftSteps[selectedStepIndex + 1]];
    selectedStepIndex += 1;
    renderDraftSteps();
  }
});
$$('input[name="stepType"]').forEach((item) =>
  item.addEventListener("change", () => {
    selectedAvailableJobId = "";
    renderStepConfig();
  }),
);
$("#openScheduleAssistant").addEventListener("click", openAssistant);
$("#closeAssistant").addEventListener("click", () => $("#assistantDialog").close());
$("#cancelAssistant").addEventListener("click", () => $("#assistantDialog").close());
$("#applyAssistant").addEventListener("click", applyAssistant);
$$('input[name="assistantMode"]').forEach((item) => item.addEventListener("change", renderAssistantMode));
$("#closeLogDialog").addEventListener("click", () => $("#logDialog").close());
$("#closeLogFooter").addEventListener("click", () => $("#logDialog").close());

refreshAll().catch((error) => setStatus(error.message, "error"));
setInterval(() => refreshSchedulesLive().catch(() => {}), 1000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshSchedulesLive().catch(() => {});
});
