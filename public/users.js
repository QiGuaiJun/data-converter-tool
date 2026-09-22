/* 账号管理页（P3，仅管理员）。
 *
 * 服务端对 /api/users* 强制 admin 角色；这里只负责展示与操作，
 * 并且把「最后一名管理员」这类约束的报错原样透出给用户。
 */
(function () {
  "use strict";

  var CSRF = { "X-DC-Request": "1" };
  var $ = function (selector) {
    return document.querySelector(selector);
  };
  var roleLabels = { viewer: "只读", operator: "可写", admin: "管理员" };
  var roleOptions = [
    { value: "viewer", label: "只读" },
    { value: "operator", label: "可写" },
    { value: "admin", label: "管理员" },
  ];
  var state = { users: [], roles: [], currentUserId: "" };

  // 导出用的列定义：[字段, 表头]。顺序即 CSV 列顺序。
  // 注意：**不含 password_hash** —— 服务端本来也不返回它，这里再显式约束一次。
  var EXPORT_COLUMNS = [
    ["username", "账号名称"],
    ["role", "角色"],
    ["enabled", "状态"],
    ["activeSessions", "在线会话"],
    ["lastLoginAt", "最近登录"],
    ["createdAt", "创建时间"],
    ["createdBy", "创建来源"],
    ["lockedUntil", "锁定至"],
  ];

  function pad2(value) {
    return (value < 10 ? "0" : "") + value;
  }

  function csvCell(value) {
    var text = String(value == null ? "" : value);
    // 含逗号/引号/换行的值必须整体加引号，否则 Excel 会串列
    if (/[",\r\n]/.test(text)) {
      text = '"' + text.replace(/"/g, '""') + '"';
    }
    return text;
  }

  function exportCsv() {
    if (!state.users.length) {
      setStatus("当前没有可导出的账号", "error");
      return;
    }
    var rows = [EXPORT_COLUMNS.map(function (item) { return item[1]; })];
    state.users.forEach(function (user) {
      rows.push(EXPORT_COLUMNS.map(function (item) {
        if (item[0] === "role") {
          return roleLabels[user.role] || user.role;
        }
        if (item[0] === "enabled") {
          return user.enabled ? "启用" : "停用";
        }
        return user[item[0]];
      }));
    });
    var csv = rows
      .map(function (row) {
        return row.map(csvCell).join(",");
      })
      .join("\r\n");
    // BOM 必须加，否则 Excel 打开中文会乱码（这是最常被投诉的一个坑）
    var blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    var now = new Date();
    var fileName =
      "账号清单-" +
      now.getFullYear() +
      pad2(now.getMonth() + 1) +
      pad2(now.getDate()) +
      "-" +
      pad2(now.getHours()) +
      pad2(now.getMinutes()) +
      ".csv";
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
    setStatus("已导出 " + state.users.length + " 个账号（不含口令）：" + fileName, "success");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setStatus(text, kind) {
    var node = $("#userStatus");
    node.textContent = text;
    node.className = kind ? "status-" + kind : "";
  }

  async function requestJson(url, options) {
    var config = options || {};
    var method = (config.method || "GET").toUpperCase();
    var headers = Object.assign({ Accept: "application/json" }, config.headers || {});
    if (method !== "GET") {
      headers = Object.assign(headers, CSRF);
    }
    var response = await fetch(url, {
      method: method,
      headers: headers,
      body: config.body,
      credentials: "same-origin",
    });
    var payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok || !payload || payload.ok !== true) {
      var message = (payload && payload.error) || ("请求失败（HTTP " + response.status + "）");
      throw new Error(message);
    }
    return payload;
  }

  function renderLegend() {
    var roles = state.roles.length ? state.roles : roleOptions;
    $("#roleLegend").innerHTML =
      "角色说明：" +
      roles
        .map(function (role) {
          return (
            '<span class="role-chip role-' +
            escapeHtml(role.value) +
            '">' +
            escapeHtml(role.label) +
            "</span>" +
            '<span class="role-desc">' +
            escapeHtml(role.description || "") +
            "</span>"
          );
        })
        .join("");
  }

  function roleSelect(user) {
    return (
      '<select class="role-select" data-role-for="' +
      escapeHtml(user.id) +
      '">' +
      roleOptions
        .map(function (role) {
          var selected = role.value === user.role ? " selected" : "";
          return '<option value="' + role.value + '"' + selected + ">" + role.label + "</option>";
        })
        .join("") +
      "</select>"
    );
  }

  function render() {
    var tbody = $("#userRows");
    if (!state.users.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无账号</td></tr>';
      return;
    }
    tbody.innerHTML = state.users
      .map(function (user) {
        var isSelf = String(user.id) === String(state.currentUserId);
        var statusText = user.enabled ? "启用中" : "已停用";
        if (user.lockedUntil) {
          statusText += "（锁定至 " + escapeHtml(user.lockedUntil) + "）";
        }
        var badgeClass = user.enabled ? "badge-on" : "badge-off";
        return (
          "<tr>" +
          "<td><strong>" +
          escapeHtml(user.username) +
          "</strong>" +
          (isSelf ? '<span class="self-tag">当前</span>' : "") +
          "</td>" +
          "<td>" +
          roleSelect(user) +
          "</td>" +
          '<td><span class="' +
          badgeClass +
          '">' +
          statusText +
          "</span></td>" +
          "<td>" +
          user.activeSessions +
          "</td>" +
          "<td>" +
          escapeHtml(user.lastLoginAt || "—") +
          "</td>" +
          "<td>" +
          escapeHtml(user.createdBy || "—") +
          "</td>" +
          '<td class="user-actions">' +
          '<button type="button" data-action="save" data-id="' +
          escapeHtml(user.id) +
          '">保存角色</button>' +
          '<button type="button" data-action="toggle" data-id="' +
          escapeHtml(user.id) +
          '">' +
          (user.enabled ? "停用" : "启用") +
          "</button>" +
          '<button type="button" data-action="reset" data-id="' +
          escapeHtml(user.id) +
          '">重置密码</button>' +
          '<button type="button" class="danger" data-action="delete" data-id="' +
          escapeHtml(user.id) +
          '">删除</button>' +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  async function load() {
    setStatus("正在读取账号列表...");
    try {
      var payload = await requestJson("/api/users");
      state.users = payload.users || [];
      state.roles = payload.roles || [];
      state.currentUserId = payload.currentUserId || "";
      renderLegend();
      render();
      setStatus("共 " + state.users.length + " 个账号", "success");
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  function findUser(id) {
    return state.users.filter(function (item) {
      return String(item.id) === String(id);
    })[0];
  }

  async function postUpdate(body) {
    var payload = await requestJson("/api/users/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setStatus(payload.message || "已更新", "success");
    await load();
  }

  function onRowClick(event) {
    var button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    var user = findUser(button.getAttribute("data-id"));
    if (!user) {
      return;
    }
    var action = button.getAttribute("data-action");
    if (action === "save") {
      var select = document.querySelector('select[data-role-for="' + user.id + '"]');
      postUpdate({ id: user.id, role: select.value }).catch(function (error) {
        setStatus(error.message, "error");
      });
      return;
    }
    if (action === "toggle") {
      postUpdate({ id: user.id, enabled: !user.enabled }).catch(function (error) {
        setStatus(error.message, "error");
      });
      return;
    }
    if (action === "reset") {
      var password = window.prompt("为 " + user.username + " 设置新密码（至少 8 位）");
      if (!password) {
        return;
      }
      postUpdate({ id: user.id, password: password }).catch(function (error) {
        setStatus(error.message, "error");
      });
      return;
    }
    if (action === "delete") {
      window
        .dcConfirm({
          title: "删除账号",
          lines: ["确认删除账号 " + user.username + " ？", "该账号的所有登录会话会立即失效。"],
          okText: "删除",
        })
        .then(function (confirmed) {
          if (!confirmed) {
            return;
          }
          return requestJson("/api/users?id=" + encodeURIComponent(user.id), { method: "DELETE" })
            .then(function (payload) {
              setStatus(payload.message || "已删除", "success");
              return load();
            })
            .catch(function (error) {
              setStatus(error.message, "error");
            });
        });
    }
  }

  function openCreateDialog() {
    var existing = document.getElementById("dcUserDialog");
    if (existing) {
      existing.remove();
    }
    var dialog = document.createElement("section");
    dialog.className = "dialog";
    dialog.id = "dcUserDialog";
    dialog.innerHTML =
      '<div class="dialog-card confirm-card">' +
      '<div class="dialog-title"><strong>新建账号</strong><button type="button" data-close>×</button></div>' +
      '<label class="dc-field">账号名称<input id="dcNewUserName" autocomplete="off" placeholder="任意字符均可（中文、符号都行），最多 32 个字符" /></label>' +
      '<label class="dc-field">初始密码<input type="password" id="dcNewUserPassword" autocomplete="new-password" /></label>' +
      '<label class="dc-field">角色<select id="dcNewUserRole"></select></label>' +
      '<p id="dcUserError" class="dc-field-error"></p>' +
      '<div class="confirm-actions">' +
      '<button type="button" class="button" data-close>取消</button>' +
      '<button type="button" class="primary" id="dcUserCreateOk">创建</button>' +
      "</div></div>";
    document.body.appendChild(dialog);
    dialog.querySelector("#dcNewUserRole").innerHTML = roleOptions
      .map(function (role) {
        var selected = role.value === "viewer" ? " selected" : "";
        return '<option value="' + role.value + '"' + selected + ">" + role.label + "</option>";
      })
      .join("");

    var errorEl = dialog.querySelector("#dcUserError");
    Array.prototype.forEach.call(dialog.querySelectorAll("[data-close]"), function (button) {
      button.addEventListener("click", function () {
        dialog.remove();
      });
    });
    dialog.querySelector("#dcUserCreateOk").addEventListener("click", function () {
      errorEl.textContent = "";
      var body = {
        username: dialog.querySelector("#dcNewUserName").value.trim(),
        password: dialog.querySelector("#dcNewUserPassword").value,
        role: dialog.querySelector("#dcNewUserRole").value,
      };
      if (!body.username || !body.password) {
        errorEl.textContent = "请填写账号名称与初始密码。";
        return;
      }
      requestJson("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (payload) {
          dialog.remove();
          setStatus(payload.message || "已创建，请把初始密码告知本人", "success");
          return load();
        })
        .catch(function (error) {
          errorEl.textContent = error.message;
        });
    });
  }

  function boot() {
    $("#refreshUsers").addEventListener("click", load);
    $("#newUser").addEventListener("click", openCreateDialog);
    $("#exportUsers").addEventListener("click", exportCsv);
    $("#userRows").addEventListener("click", onRowClick);
    load();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
