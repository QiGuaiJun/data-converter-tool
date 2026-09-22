/* 审计日志页（P3，仅管理员）。
 *
 * 服务端 /api/audit-logs 已按角色限制为 admin，并支持按用户/动作/结果/关键字过滤与分页。
 * 保留期由服务端 DC_AUDIT_RETENTION_DAYS 决定（默认 90 天），这里只做展示。
 */
(function () {
  "use strict";

  var PAGE_SIZE = 100;
  var $ = function (selector) {
    return document.querySelector(selector);
  };
  var state = { offset: 0, total: 0, loading: false };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setStatus(text, kind) {
    var node = $("#auditStatusText");
    node.textContent = text;
    node.className = kind ? "status-" + kind : "";
  }

  function currentFilters() {
    return {
      username: $("#auditUser").value,
      action: $("#auditAction").value,
      status: $("#auditStatus").value,
      keyword: $("#auditKeyword").value.trim(),
    };
  }

  function buildQuery() {
    var filters = currentFilters();
    var parts = ["limit=" + PAGE_SIZE, "offset=" + state.offset];
    Object.keys(filters).forEach(function (key) {
      if (filters[key]) {
        parts.push(key + "=" + encodeURIComponent(filters[key]));
      }
    });
    return parts.join("&");
  }

  function fillOptions(select, values) {
    var current = select.value;
    var head = select.querySelector("option").outerHTML;
    select.innerHTML = head + values.map(function (value) {
      return '<option value="' + escapeHtml(value) + '">' + escapeHtml(value) + "</option>";
    }).join("");
    if (values.indexOf(current) >= 0) {
      select.value = current;
    }
  }

  function renderRows(logs) {
    var tbody = $("#auditRows");
    if (!logs.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">没有符合条件的记录</td></tr>';
      return;
    }
    tbody.innerHTML = logs
      .map(function (log) {
        var statusClass = log.status === "ok" ? "badge-on" : "badge-off";
        var statusText = { ok: "成功", denied: "被拒绝", failed: "失败" }[log.status] || log.status;
        return (
          "<tr>" +
          "<td>" + escapeHtml(log.created_at) + "</td>" +
          "<td>" + escapeHtml(log.username || "—") + "</td>" +
          "<td>" + escapeHtml(log.action) + "</td>" +
          "<td>" + escapeHtml(log.target || "—") + "</td>" +
          "<td class='audit-detail'>" + escapeHtml(log.detail || "—") + "</td>" +
          "<td>" + escapeHtml(log.ip || "—") + "</td>" +
          "<td><span class='" + statusClass + "'>" + statusText + "</span></td>" +
          "</tr>"
        );
      })
      .join("");
  }

  async function load(reset) {
    if (state.loading) {
      return;
    }
    if (reset) {
      state.offset = 0;
    }
    state.loading = true;
    setStatus("正在读取...");
    try {
      var response = await fetch("/api/audit-logs?" + buildQuery(), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      var payload = await response.json();
      if (!response.ok || payload.ok !== true) {
        throw new Error((payload && payload.error) || ("HTTP " + response.status));
      }
      state.total = payload.total || 0;
      renderRows(payload.logs || []);
      if (payload.users) {
        fillOptions($("#auditUser"), payload.users);
      }
      if (payload.actions) {
        fillOptions($("#auditAction"), payload.actions);
      }
      var page = Math.floor(state.offset / PAGE_SIZE) + 1;
      var pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
      $("#auditPageInfo").textContent = "第 " + page + " / " + pages + " 页，共 " + state.total + " 条";
      $("#auditPrev").disabled = state.offset <= 0;
      $("#auditNext").disabled = state.offset + PAGE_SIZE >= state.total;
      if (payload.retentionDays) {
        $("#auditRetention").textContent = "审计记录保留 " + payload.retentionDays + " 天（到期自动清理）";
      }
      setStatus("共 " + state.total + " 条记录", "success");
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      state.loading = false;
    }
  }

  function boot() {
    $("#refreshAudit").addEventListener("click", function () {
      load(true);
    });
    $("#auditKeyword").addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        load(true);
      }
    });
    ["#auditUser", "#auditAction", "#auditStatus"].forEach(function (selector) {
      $(selector).addEventListener("change", function () {
        load(true);
      });
    });
    $("#auditPrev").addEventListener("click", function () {
      state.offset = Math.max(0, state.offset - PAGE_SIZE);
      load(false);
    });
    $("#auditNext").addEventListener("click", function () {
      state.offset += PAGE_SIZE;
      load(false);
    });
    load(true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
