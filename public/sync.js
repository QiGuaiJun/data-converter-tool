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
        var off = t.enabled === false ? '<i class="off-tag">已停用</i>' : "";
        return '<tr data-id="' + esc(t.id) + '">' +
          '<td class="c-name">' + esc(t.name) + off + "</td>" +
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
    if (!STATE.selectedId) {
      el("runCount").textContent = "";
      box.textContent = "选中一个任务后显示它的运行记录";
      return;
    }
    var runs = STATE.runs || [];
    el("runCount").textContent = runs.length ? runs.length + " 条" : "";
    if (!runs.length) { box.innerHTML = '<div class="sync-empty">这个任务还没有运行记录。</div>'; return; }
    box.innerHTML = runs.map(function (r) {
      var cls = r.status === "成功" ? "ok" : "bad";
      var rows = ["读取 " + fmt(r.rowsRead) + " 行", "写入 " + fmt(r.rowsWritten) + " 行"];
      if (r.rowsUpdated) rows.push("更新 " + fmt(r.rowsUpdated) + " 行");
      rows.push("耗时 " + (r.elapsedMs / 1000).toFixed(1) + " 秒");
      return '<div class="run-row">' +
        '<div class="run-top"><span class="st ' + cls + '">' + esc(r.status) + "</span>" +
        '<span class="muted">' + esc(r.startedAt) + "</span>" +
        '<span class="muted">' + esc(r.syncModeLabel) + "</span></div>" +
        '<div class="run-meta">' + esc(rows.join(" · ")) + "</div>" +
        '<div class="run-meta">' + esc(r.sourceLabel) + " → " + esc(r.targetLabel) + "</div>" +
        (r.message ? '<div class="run-err">' + esc(r.message) + "</div>" : "") +
        "</div>";
    }).join("");
  }

  function select(id) {
    STATE.selectedId = id || "";
    paintSelection();
    STATE.runs = [];
    renderRuns();
    if (!STATE.selectedId) return Promise.resolve();
    return requestJson("/api/sync/runs?taskId=" + encodeURIComponent(STATE.selectedId) + "&limit=10")
      .then(function (payload) { STATE.runs = payload.runs || []; renderRuns(); })
      .catch(function (err) { el("runList").textContent = "读取运行历史失败：" + err.message; });
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
      "会同时清掉它的水位记录与运行历史，<b>不可撤销</b>。目标表里的数据不会被删。",
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

  el("taskList").addEventListener("click", function (e) {
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
      if (STATE.selectedId && !taskById(STATE.selectedId)) STATE.selectedId = "";
      if (!STATE.selectedId && STATE.tasks.length) STATE.selectedId = STATE.tasks[0].id;
      render();
      return select(STATE.selectedId);
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
