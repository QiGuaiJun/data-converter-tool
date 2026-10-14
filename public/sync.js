/* ==========================================================================
   同步模块 · 模块层（任务列表 + 视图切换）
   --------------------------------------------------------------------------
   同步模块只有**一个页面**，里面两个视图：
     #listView   任务列表 —— 打开同步 / 新建 / 删除 / 双击打开
     #canvasView 画布     —— 五个节点、右侧配置面板、预览/保存/执行

   为什么不做成两个页面（2026-10-10 业主反馈）：
     ① 点「新建」跳页会触发 shell.js 的 beforeunload 提醒（"离开此网站？"），
        而这两层本来就在同一个模块里，用户没打算离开；
     ② 分开成两个页面后画布那页没有 ribbon / 侧栏，和别的模块风格对不上；
     ③ 同页切换天然解决了"返回按钮该长什么样"—— 它就是一个普通工具条按钮。

   画布逻辑全在 sync-canvas.js，通过 window.SyncCanvas 调用。
   ========================================================================== */
(function () {
  "use strict";

  var STATE = { tasks: [], connections: [], selectedId: "" };

  function el(id) { return document.getElementById(id); }
  function esc(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmt(n) { return Number(n || 0).toLocaleString("en-US"); }

  /* 非 JSON 基本只有两个来源：服务端还是旧版本（没这个接口 → 404 HTML），
     或请求被别的服务挡了。只说"不是 JSON"用户无从下手，得把方法与状态码摆出来。 */
  function dcRequestHint(method, url, status, hint) {
    return method + " " + url + " 返回 HTTP " + status + "，内容不是 JSON。" + (hint || "");
  }

  function requestJson(url, options) {
    var opts = options || {};
    var method = opts.method || "GET";
    opts.headers = Object.assign(
      { "Content-Type": "application/json", "X-DC-Request": "1", Accept: "application/json" },
      opts.headers || {}
    );
    return fetch(url, opts).then(function (response) {
      return response.text().then(function (text) {
        var payload = null;
        try { payload = JSON.parse(text); } catch (err) { payload = null; }
        if (!payload) {
          var hint = response.status === 404
            ? "（接口不存在 —— 多半是导表工具还在跑旧版本，关掉那个黑窗口重新双击「启动导表工具.bat」）"
            : "";
          throw new Error(dcRequestHint(method, url, response.status, hint));
        }
        if (payload.ok === false) throw new Error(payload.error || "请求失败。");
        return payload;
      });
    });
  }

  function connLabel(connId) {
    var hit = STATE.connections.filter(function (c) { return c.id === connId; })[0];
    return hit ? (hit.name || "") + " · " + (hit.database || "") : "";
  }
  function dbOf(connId) {
    var hit = STATE.connections.filter(function (c) { return c.id === connId; })[0];
    return hit ? (hit.database || "") : "";
  }
  function sourceText(t) {
    if (t.sourceMode === "sql") return "自定义 SQL";
    return (dbOf(t.sourceConnectionId) ? dbOf(t.sourceConnectionId) + "." : "") +
      (t.sourceTable || "（未选表）");
  }
  function targetText(t) {
    return (dbOf(t.targetConnectionId) ? dbOf(t.targetConnectionId) + "." : "") +
      (t.targetTable || t.sourceTable || "（未填表）");
  }
  function lastRunText(t) {
    if (!t.lastRunAt) return '<span class="muted">从未运行</span>';
    var cls = t.lastStatus === "成功" ? "ok" : (t.lastStatus ? "bad" : "muted");
    var rows = t.lastRowsWritten ? " · 写入 " + fmt(t.lastRowsWritten) + " 行" : "";
    return '<span class="st ' + cls + '">' + esc(t.lastStatus || "—") + "</span> " +
      '<span class="muted">' + esc(t.lastRunAt) + rows + "</span>";
  }

  /* ---------------------------------------------------------------- 视图切换 */
  function showView(name) {
    el("listView").classList.toggle("on", name === "list");
    el("canvasView").classList.toggle("on", name === "canvas");
  }
  function openCanvas(task) {
    showView("canvas");
    // 画布视图必须先显示再 load —— fitView 要读节点尺寸，隐藏时读到的全是 0。
    window.SyncCanvas.load(task);
  }

  /* ---------------------------------------------------------------- 渲染 */
  /* ⚠️ 选中态必须**局部更新**，不能重建列表。
     重建（innerHTML=…）会把正在被点的那一行从 DOM 里换掉，第二次点击 /
     dblclick 就落在已移除的节点上 —— 浏览器根本不会在游离元素上派发事件，
     表现就是"双击没反应"。这跟画布面板那个 input 重绘的坑是同一类错误。 */
  function paintSelection() {
    var rows = document.querySelectorAll("#taskList tr[data-id]");
    Array.prototype.forEach.call(rows, function (r) {
      r.classList.toggle("on", r.dataset.id === STATE.selectedId);
    });
    var has = !!STATE.selectedId;
    el("openTask").disabled = !has;
    el("deleteTask").disabled = !has;
  }

  function render() {
    var box = el("taskList");
    el("taskCount").textContent = STATE.tasks.length + " 个";
    if (!STATE.tasks.length) {
      box.innerHTML = '<div class="sync-empty">还没有同步任务。点左上角「新建同步任务」开始配置。</div>';
    } else {
      box.innerHTML = STATE.tasks.map(function (t) {
        // 「已停用」原来贴在任务名后面，现在单独一列并做成可点的开关
        return '<tr data-id="' + esc(t.id) + '">' +
          '<td class="c-name">' + esc(t.name) + "</td>" +
          '<td class="c-ops"><div class="row-ops">' +
          '<button type="button" class="state-toggle' +
          (t.enabled === false ? " off" : "") + '" data-id="' + esc(t.id) + '" title="点击' +
          (t.enabled === false ? "启用" : "停用") + '这个任务">' +
          (t.enabled === false ? "已停用" : "启用中") + "</button>" +
          '<button type="button" class="row-run" data-id="' + esc(t.id) +
          '" title="按已保存的配置跑一次">运行</button>' +
          "</div></td>" +
          '<td class="c-src" title="' + esc(connLabel(t.sourceConnectionId)) + '">' + esc(sourceText(t)) + "</td>" +
          '<td class="c-dst" title="' + esc(connLabel(t.targetConnectionId)) + '">' + esc(targetText(t)) + "</td>" +
          '<td class="c-mode">' + esc(t.syncModeLabel || t.syncMode) + "</td>" +
          '<td class="c-run">' + lastRunText(t) + "</td>" +
          "</tr>";
      }).join("");
    }
    paintSelection();
  }

  function renderRuns() {
    var box = el("runList");
    var runs = STATE.runs || [];
    // 标题要把范围说清楚：默认是全量（所有任务），选中某一行才收窄。
    // 不写范围的话，用户看到一列混杂的记录会以为是当前任务的。
    el("runCount").textContent = runs.length
      ? (STATE.selectedId ? "该任务最近 " + runs.length + " 条" : "全部任务最近 " + runs.length + " 条")
      : "";
    if (!runs.length) {
      box.innerHTML = '<div class="sync-empty">' +
        (STATE.selectedId ? "这个任务还没有运行记录。" : "还没有任何同步任务的运行记录。") + "</div>";
      return;
    }
    box.innerHTML = runs.map(runHtml).join("");
  }

  /* 后端把一次运行的结论压成了一行，用 `·` 当分隔符
     （"全量覆盖 · 同步范围：源表全量（整表）· 读取 51 行、新增 51 行、…"），
     老记录也是这么存的，所以在渲染前把 `·` 拆成换行，再交给 DCLog.lines 逐行处理。 */
  function splitLogMessage(text) {
    /* 顺手把"已经在元信息网格里出现过"的片段丢掉 —— 模式、读取/写入/更新行数、
       耗时、目标表合计这些，网格里每项都有一格，正文再来一遍就是重复信息。
       剩下的才是真正只有正文才说得清的：同步范围、建表结论、错误。 */
    var duplicated = /^(模式[:：]|全量覆盖$|读取\s|写入\s|新增\s|更新\s|耗时\s|目标表合计)/;
    // `。` 也要拆：后端是「同步范围：源表全量（清空目标后重写）。读取 1 行、新增 1 行…」，
    // 只按 `·、` 拆的话「。读取 1 行」会连着同步范围一起留在正文里，又重复一遍。
    return String(text || "")
      .split(/[。·、]/)
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s && !duplicated.test(s); })
      .join("\n");
  }

  /* 单条运行记录。格式对齐**作业模块那套**（业主 2026-10-08 拍板的）：
     可折叠 + 元信息一行一个维度、标签加粗成列 + 正文按行渲染。
     之前这里是 sync 模块自己拼的，把模式/范围/读取/新增/耗时全塞进一个 div，
     同样的数字在一屏里说了两遍，业主反馈"日志格式并没有之前确定好的格式"。 */
  function runHtml(r, index) {
    var L = window.DCLog;
    var summary = "读取 " + fmt(r.rowsRead) + " 行 · 写入 " + fmt(r.rowsWritten) + " 行";
    if (r.rowsUpdated) summary += " · 更新 " + fmt(r.rowsUpdated) + " 行";
    summary += " · 耗时 " + L.humanMs(r.elapsedMs);

    var meta = [
      ["模式", r.syncModeLabel || r.syncMode],
      ["读取", fmt(r.rowsRead) + " 行"],
      ["写入", fmt(r.rowsWritten) + " 行"]
    ];
    if (r.rowsUpdated) meta.push(["更新", fmt(r.rowsUpdated) + " 行"]);
    meta.push(["目标表", (r.targetLabel || "") + " · 合计 " + fmt(r.targetRows) + " 行"]);
    meta.push(["耗时", L.humanMs(r.elapsedMs)]);

    // 折叠头带任务名 —— 默认展示的是**所有任务**的日志，不带名字分不清哪条是谁跑的。
    // 结构也和作业模块对齐：<strong>名称<span>状态</span></strong>。
    return '<details class="log-item ' + L.stateClass(r.status) + '"' + (index === 0 ? " open" : "") + ">" +
      "<summary><strong>" + esc(r.taskName || "（任务已删除）") +
      "<span>" + esc(r.status) + "</span></strong>" +
      '<em class="log-summary">' + esc(summary) + "</em>" +
      "<time>" + esc(r.startedAt) + "</time></summary>" +
      '<div class="log-body">' +
      '<div class="run-meta-grid">' + meta.map(function (pair) {
        return "<span><b>" + esc(pair[0]) + "</b>" + esc(pair[1]) + "</span>";
      }).join("") + "</div>" +
      '<div class="run-message">' + L.lines(
        "源：" + (r.sourceLabel || "") +
        "\n目标：" + (r.targetLabel || "") +
        (r.message ? "\n" + splitLogMessage(r.message) : "")
      ) + "</div>" +
      "</div></details>";
  }

  /* 运行日志：**没选中任务就拉全部任务的**（后端 list_sync_runs 收到空 taskId 就是全量，
     返回里也带 taskName），选中了才收窄到那一个。
     原先 loadTasks 会强制选中第一个任务，于是这块永远只显示一个任务的记录，
     业主反馈"运行日志应该是所有任务的日志信息"。 */
  function loadRuns() {
    var url = "/api/sync/runs?limit=50";
    if (STATE.selectedId) url += "&taskId=" + encodeURIComponent(STATE.selectedId);
    return requestJson(url)
      .then(function (payload) { STATE.runs = payload.runs || []; renderRuns(); })
      .catch(function (err) { el("runList").textContent = "读取运行日志失败：" + err.message; });
  }

  function select(id) {
    STATE.selectedId = id || "";
    paintSelection();
    STATE.runs = [];
    renderRuns();
    return loadRuns();
  }
  function taskById(id) {
    return STATE.tasks.filter(function (t) { return t.id === id; })[0] || null;
  }

  /* ---------------------------------------------------------------- 对话框 */
  /* 一个通用弹窗：只看结果时 footer 只有一个「知道了」；需要确认时换成「取消 / 确认」。
     ⚠️ 用页内弹窗而不是 window.confirm —— 原生 confirm 会阻塞整个页面。 */
  function showDialog(title, html, confirmText, onConfirm) {
    el("listDlgTitle").textContent = title;
    el("listDlgBody").innerHTML = html;
    var foot = el("listDlgFoot");
    if (!confirmText) {
      foot.innerHTML = '<button id="listDlgOk" class="primary" type="button">知道了</button>';
    } else {
      foot.innerHTML = '<button id="listDlgCancel" type="button">取消</button>' +
        '<button id="listDlgOk" class="primary danger" type="button">' + esc(confirmText) + "</button>";
      el("listDlgCancel").onclick = function () { el("listDialog").close(); };
    }
    el("listDlgOk").onclick = function () {
      el("listDialog").close();
      if (confirmText && onConfirm) onConfirm();
    };
    el("listDialog").showModal();
  }
  el("listDlgClose").onclick = function () { el("listDialog").close(); };

  /* ---------------------------------------------------------------- 动作 */
  el("newTask").onclick = function () { openCanvas(null); };
  el("openTask").onclick = function () {
    var t = taskById(STATE.selectedId);
    if (t) openCanvas(t);
  };
  /* 画布里也要能看到运行日志 —— 之前跑完「立即同步」只弹个结果框，
     想翻历史得先退回列表。这里直接拉最近 20 条，不跳页。 */
  function runLine(r) {
    var rows = ["读取 " + fmt(r.rowsRead) + " 行", "写入 " + fmt(r.rowsWritten) + " 行"];
    if (r.rowsUpdated) rows.push("更新 " + fmt(r.rowsUpdated) + " 行");
    rows.push("耗时 " + (r.elapsedMs / 1000).toFixed(1) + " 秒");
    return '<div class="ln"><span>' + esc(r.startedAt) + " · " + esc(r.status) + "</span><span>" +
      esc(rows.join(" · ")) + "</span></div>" +
      (r.message ? '<div class="ln"><span>错误</span><span>' + esc(r.message) + "</span></div>" : "");
  }
  el("cbRuns").onclick = function () {
    var id = window.SyncCanvas.currentId();
    if (!id) {
      showDialog("还没有运行日志", "这个任务还没保存过。先点「保存任务」，之后每跑一次都会在这里留一条记录。");
      return;
    }
    requestJson("/api/sync/runs?taskId=" + encodeURIComponent(id) + "&limit=20")
      .then(function (payload) {
        var runs = payload.runs || [];
        if (!runs.length) { showDialog("运行日志", "这个任务还没有运行记录。点「立即同步」跑一次试试。"); return; }
        showDialog("运行日志（最近 " + runs.length + " 条）", runs.map(runLine).join(""));
      })
      .catch(function (err) { showDialog("读取运行日志失败", esc(err.message)); });
  };

  el("backToList").onclick = function () {
    if (window.SyncCanvas.isDirty()) {
      showDialog("还有未保存的修改",
        "「<b>" + esc(window.SyncCanvas.currentName() || "未命名任务") + "</b>」改过但还没保存。<br><br>" +
        "返回列表不会自动保存。可以直接丢弃，也可以先回画布点「保存任务」。",
        "丢弃并返回", function () { showView("list"); loadTasks(); });
      return;
    }
    showView("list");
    loadTasks();
  };

  el("deleteTask").onclick = function () {
    var t = taskById(STATE.selectedId);
    if (!t) return;
    showDialog("删除同步任务",
      "即将删除任务 <b>" + esc(t.name) + "</b>。<br><br>" +
      "会同时清掉它的水位记录与运行日志，<b>不可撤销</b>。目标表里的数据不会被删。",
      "确认删除", function () {
        requestJson("/api/sync/tasks?id=" + encodeURIComponent(t.id), { method: "DELETE" })
          .then(function () {
            STATE.selectedId = "";
            return loadTasks();
          })
          .catch(function (err) { showDialog("删除失败", esc(err.message)); });
      });
  };

  /* 「立即同步」和「刷新」已从模块层移除（业主 2026-10-10：这一层只留三个按钮）。
     执行同步的入口只剩画布里的「立即同步」；列表在删除 / 保存后会自动重载。 */

  /* 列表页直接切「启用 / 停用」，不用为了停一个任务双击进画布。
     提交时不带 layout —— 后端那边是 coalesce(?, layout_json)，不会把画布布局冲掉。 */
  function toggleTaskEnabled(t) {
    var next = t.enabled === false;
    var payload = {
      id: t.id, name: t.name,
      sourceConnectionId: t.sourceConnectionId, sourceMode: t.sourceMode,
      sourceTable: t.sourceTable, sourceSql: t.sourceSql,
      targetConnectionId: t.targetConnectionId, targetTable: t.targetTable,
      columns: t.columns, syncMode: t.syncMode, keyColumns: t.keyColumns,
      watermarkColumn: t.watermarkColumn, batchRows: t.batchRows,
      commitMode: t.commitMode, stopOnError: t.stopOnError,
      enabled: next
    };
    el("listStatus").textContent = next ? "正在启用…" : "正在停用…";
    requestJson("/api/sync/tasks", { method: "POST", body: JSON.stringify(payload) })
      .then(function () { el("listStatus").textContent = ""; return loadTasks(); })
      .catch(function (err) {
        el("listStatus").textContent = "";
        showDialog("切换失败", esc(err.message));
      });
  }

  /* 列表页直接跑，不必为了跑一次双击进画布再点「立即同步」。
     提交的是整条任务对象 —— 后端 execute_sync_task 吃的就是完整配置。 */
  function runTaskNow(t, btn) {
    showDialog("立即同步",
      "按已保存的配置跑一次：<br><br>" +
      "· 任务：<b>" + esc(t.name) + "</b><br>" +
      "· 源：" + esc(sourceText(t)) + "<br>" +
      "· 目标：" + esc(targetText(t)) + "<br>" +
      "· 模式：" + esc(t.syncModeLabel || t.syncMode) + "<br><br>" +
      "会写入目标表。确定继续？",
      "开始同步", function () {
        if (btn) { btn.disabled = true; btn.textContent = "运行中"; }
        el("listStatus").textContent = "正在同步…";
        requestJson("/api/sync/run", { method: "POST", body: JSON.stringify(t) })
          .then(function (payload) {
            var run = payload.run;
            el("listStatus").textContent = "";
            showDialog("同步完成",
              '<div class="ln"><span>模式</span><span>' + esc(run.syncModeLabel) + "</span></div>" +
              '<div class="ln"><span>读取</span><span>' + fmt(run.rowsRead) + " 行</span></div>" +
              '<div class="ln"><span>新增</span><span>' + fmt(run.rowsWritten) + " 行</span></div>" +
              (run.rowsUpdated ? '<div class="ln"><span>更新</span><span>' + fmt(run.rowsUpdated) + " 行</span></div>" : "") +
              '<div class="ln"><span>目标表</span><span>' + esc(run.targetLabel) + " · 合计 " + fmt(run.targetRows) + " 行</span></div>" +
              '<div class="ln"><span>耗时</span><span>' + (run.elapsedMs / 1000).toFixed(1) + " 秒</span></div>");
            return loadTasks();
          })
          .catch(function (err) {
            el("listStatus").textContent = "";
            showDialog("同步失败", esc(err.message));
            loadTasks();
          })
          .then(function () { if (btn) { btn.disabled = false; btn.textContent = "运行"; } });
      });
  }

  el("taskList").addEventListener("click", function (e) {
    var toggle = e.target.closest(".state-toggle");
    if (toggle) {
      e.stopPropagation(); // 别让这一下顺带把行选中/打开
      var t = taskById(toggle.dataset.id);
      if (t) toggleTaskEnabled(t);
      return;
    }
    var runBtn = e.target.closest(".row-run");
    if (runBtn) {
      e.stopPropagation();
      var t2 = taskById(runBtn.dataset.id);
      if (t2) runTaskNow(t2, runBtn);
      return;
    }
    var row = e.target.closest("tr[data-id]");
    if (!row) return;
    select(row.dataset.id);
  });
  el("taskList").addEventListener("dblclick", function (e) {
    var row = e.target.closest("tr[data-id]");
    if (!row) return;
    openCanvas(taskById(row.dataset.id));
  });

  /* ---------------------------------------------------------------- 启动 */
  function loadTasks() {
    el("listStatus").textContent = "正在加载…";
    return Promise.all([
      requestJson("/api/sync/tasks").then(function (payload) { STATE.tasks = payload.tasks || []; }),
      requestJson("/api/connections").then(function (payload) { STATE.connections = payload.connections || []; })
    ]).then(function () {
      el("listStatus").textContent = "";
      // 选中的任务可能已经被删掉（或刚删完），收敛一下再渲染。
      // **不再自动选中第一个任务** —— 那样运行日志就被锁死在一个任务上，
      // 而这块的默认语义是"所有任务的日志"（选中某一行的操作本身就是收窄）。
      if (STATE.selectedId && !taskById(STATE.selectedId)) STATE.selectedId = "";
      render();
      return loadRuns();
    }).catch(function (err) {
      el("listStatus").textContent = "";
      var msg = err.message || "";
      // 404 = 这一版服务端根本没有同步接口，标题直接说版本问题，别让用户去猜"加载失败"。
      showDialog(msg.indexOf("HTTP 404") >= 0 ? "服务端版本过旧" : "加载失败", esc(msg));
    });
  }

  // 画布保存成功后回来刷新列表 —— 但不切视图，用户还在画布里。
  window.SyncCanvas.onSaved = function (task) {
    var keep = task && task.id ? task.id : STATE.selectedId;
    loadTasks().then(function () {
      if (keep) { STATE.selectedId = keep; paintSelection(); }
    });
  };

  loadTasks();
})();
