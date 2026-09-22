/* 全站共享的页面级行为，所有 HTML 页面统一引入。
 *
 * 1) 改进G「素色模式」：关闭背景壁纸改用纯色画布，选择持久化到 localStorage。
 *    默认仍是壁纸（保持与历史外观一致），只是把开关交到用户手里。
 * 2) 改进E「关闭提醒」：有启用中的定时任务时，关闭/离开页面给出提示，
 *    避免用户以为「关掉页面任务还在跑」。站内跳转不提示（见 internalNavigation）。
 * 3) window.dcConfirm({title, lines, okText}) → Promise<boolean>
 *    全站通用确认框（按需注入，复用 index.html 的 .dialog / .confirm-card 样式）。
 *    查询页执行高危 SQL 前用它做二次确认——服务端仍会校验一次性令牌，前端只是第一道门。
 */
(function () {
  "use strict";

  var PLAIN_MODE_KEY = "dc_plain_mode_v1";

  function applyPlainMode(enabled) {
    if (document.body) {
      document.body.classList.toggle("plain-mode", Boolean(enabled));
    }
  }

  function readPlainMode() {
    try {
      return window.localStorage.getItem(PLAIN_MODE_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function writePlainMode(enabled) {
    try {
      window.localStorage.setItem(PLAIN_MODE_KEY, enabled ? "1" : "0");
    } catch (_) {
      /* 隐私模式下 localStorage 不可写：开关仅本次生效，不影响功能 */
    }
  }

  function initAppearance() {
    var enabled = readPlainMode();
    applyPlainMode(enabled);

    var button = document.createElement("button");
    button.type = "button";
    button.id = "plainModeToggle";
    button.className = "plain-mode-toggle";

    function sync() {
      button.textContent = enabled ? "壁纸" : "素色";
      button.title = enabled
        ? "当前为素色模式（无背景壁纸），点击恢复壁纸"
        : "当前为壁纸模式，点击切换为素色模式（去背景图，观感更干净）";
      button.setAttribute("aria-pressed", enabled ? "true" : "false");
      button.setAttribute("aria-label", button.title);
    }

    sync();
    button.addEventListener("click", function () {
      enabled = !enabled;
      applyPlainMode(enabled);
      writePlainMode(enabled);
      sync();
    });
    document.body.appendChild(button);
  }

  // 站内跳转（点侧边栏/按钮切页面）不触发关闭提醒，否则每次换页都弹窗。
  var internalNavigation = false;
  document.addEventListener(
    "click",
    function (event) {
      var target = event.target;
      if (!target || !target.closest) {
        return;
      }
      var link = target.closest("a[href]");
      if (!link) {
        return;
      }
      var href = link.getAttribute("href") || "";
      if (!href || href.charAt(0) === "#" || href.indexOf("://") >= 0 || href.indexOf("mailto:") === 0) {
        return;
      }
      internalNavigation = true;
    },
    true
  );

  function initCloseWarning() {
    fetch("/api/schedules")
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        var schedules = (payload && payload.schedules) || [];
        var hasEnabled = schedules.some(function (item) {
          return item && item.enabled;
        });
        if (!hasEnabled) {
          return;
        }
        window.addEventListener("beforeunload", function (event) {
          if (internalNavigation) {
            return undefined;
          }
          var message = "当前有启用中的定时任务，关闭此页面后服务若一并停止，任务将不再执行。确认离开？";
          event.preventDefault();
          event.returnValue = message;
          return message;
        });
      })
      .catch(function () {
        /* 拿不到定时任务列表时不打扰用户 */
      });
  }

  // ---------------------------------------------------------------- 通用确认框

  function ensureConfirmDialog() {
    var dialog = document.getElementById("dcConfirmDialog");
    if (dialog) {
      return dialog;
    }
    dialog = document.createElement("section");
    dialog.id = "dcConfirmDialog";
    dialog.className = "dialog hidden";
    dialog.innerHTML =
      '<div class="dialog-card confirm-card">' +
      '<div class="dialog-title"><strong>操作确认</strong>' +
      '<button id="dcConfirmClose" type="button">×</button></div>' +
      '<div id="dcConfirmBody"></div>' +
      '<div class="confirm-actions">' +
      '<button id="dcConfirmCancel" type="button" class="button">取消</button>' +
      '<button id="dcConfirmOk" type="button" class="primary">确认继续</button>' +
      "</div></div>";
    document.body.appendChild(dialog);
    return dialog;
  }

  /**
   * @param {{title?: string, lines?: string[], okText?: string, cancelText?: string}} options
   * @returns {Promise<boolean>} 用户是否确认
   */
  window.dcConfirm = function (options) {
    var config = options || {};
    var dialog = ensureConfirmDialog();
    var titleEl = dialog.querySelector(".dialog-title strong");
    var bodyEl = dialog.querySelector("#dcConfirmBody");
    var okButton = dialog.querySelector("#dcConfirmOk");
    var cancelButton = dialog.querySelector("#dcConfirmCancel");
    var closeButton = dialog.querySelector("#dcConfirmClose");

    titleEl.textContent = config.title || "操作确认";
    okButton.textContent = config.okText || "确认继续";
    cancelButton.textContent = config.cancelText || "取消";
    bodyEl.innerHTML = "";
    (config.lines || []).forEach(function (line) {
      var paragraph = document.createElement("p");
      paragraph.className = "confirm-message";
      paragraph.textContent = line;
      bodyEl.appendChild(paragraph);
    });

    dialog.classList.remove("hidden");
    return new Promise(function (resolve) {
      function finish(result) {
        dialog.classList.add("hidden");
        okButton.removeEventListener("click", onOk);
        cancelButton.removeEventListener("click", onCancel);
        closeButton.removeEventListener("click", onCancel);
        resolve(result);
      }
      function onOk() {
        finish(true);
      }
      function onCancel() {
        finish(false);
      }
      okButton.addEventListener("click", onOk);
      cancelButton.addEventListener("click", onCancel);
      closeButton.addEventListener("click", onCancel);
    });
  };

  // ---------------------------------------------------------------- 登录态与用户区

  var AUTH_ME_URL = "/api/auth/me";
  var currentUser = null;

  function redirectToLogin() {
    if (window.location.pathname === "/login.html") {
      return;
    }
    var here = window.location.pathname + window.location.search;
    window.location.href = "/login.html?next=" + encodeURIComponent(here);
  }

  // 写请求统一带上 CSRF 校验头：Cookie 会话下服务端强制要求它，
  // 这样既不用改 20 多个页面的 fetch 调用，也不会漏掉任何一处。
  var WRITE_METHODS = { POST: 1, PUT: 1, PATCH: 1, DELETE: 1 };

  function withSecurityHeaders(input, init) {
    var options = init ? Object.assign({}, init) : {};
    var method = String(options.method || (input && input.method) || "GET").toUpperCase();
    if (!WRITE_METHODS[method]) {
      return options;
    }
    var headers = new Headers(options.headers || (input && input.headers) || {});
    if (!headers.has("X-DC-Request")) {
      headers.set("X-DC-Request", "1");
    }
    options.headers = headers;
    return options;
  }

  // 接口返回 401（未登录 / 会话过期）时统一跳登录页。
  // 用 rawFetch 发出真实请求，避免递归。
  var rawFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    return rawFetch(input, withSecurityHeaders(input, init)).then(function (response) {
      if (response.status !== 401) {
        return response;
      }
      var url = typeof input === "string" ? input : (input && input.url) || "";
      // /api/auth/me 与 /api/auth/logout 的 401 属于正常语义，交给调用方处理
      if (url.indexOf("/api/auth/me") >= 0 || url.indexOf("/api/auth/logout") >= 0) {
        return response;
      }
      redirectToLogin();
      return response;
    });
  };

  function roleLabel(role) {
    var labels = { admin: "管理员", operator: "可写", viewer: "只读" };
    return labels[role] || role || "";
  }

  function renderUserBox(user) {
    var box = document.createElement("div");
    box.className = "auth-user-box";
    box.innerHTML =
      '<span class="auth-user-name"></span>' +
      '<span class="auth-user-role"></span>' +
      '<button type="button" class="auth-password">改密码</button>' +
      '<button type="button" class="auth-logout">退出</button>';
    box.querySelector(".auth-user-name").textContent = user.displayName || user.username;
    box.querySelector(".auth-user-role").textContent = roleLabel(user.role);
    box.querySelector(".auth-password").addEventListener("click", openPasswordDialog);
    box.querySelector(".auth-logout").addEventListener("click", function () {
      window.dcConfirm({
        title: "退出登录",
        lines: ["确认退出当前账号？"],
        okText: "退出",
      }).then(function (confirmed) {
        if (!confirmed) {
          return;
        }
        rawFetch("/api/auth/logout", { method: "POST", headers: { "X-DC-Request": "1" } }).then(function () {
          window.location.href = "/login.html";
        });
      });
    });
    document.body.appendChild(box);
  }

  function initAuth() {
    rawFetch(AUTH_ME_URL, { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (response.status === 401) {
          redirectToLogin();
          return null;
        }
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        // authEnabled=false 表示本机未启用认证，不显示用户区、不跳转
        if (!payload || !payload.ok || payload.authEnabled === false) {
          return;
        }
        currentUser = payload.user || null;
        var role = (currentUser && currentUser.role) || "";
        document.documentElement.setAttribute("data-user-role", role);
        if (currentUser) {
          renderUserBox(currentUser);
          applyRoleVisibility(role);
        }
        window.dcUser = currentUser;
      })
      .catch(function () {
        /* 探测登录态失败不打扰用户 */
      });
  }

  // ---------------------------------------------------------------- 角色门禁与改密

  // 页面 → 最低角色。服务端已经逐接口鉴权，这里只是不让用户点了才被拒。
  var PAGE_MIN_ROLE = {
    "/": "operator",
    "/index.html": "operator",
    "/export.html": "operator",
    "/sync.html": "operator",
    "/jobs.html": "operator",
    "/schedule.html": "operator",
    "/connections.html": "operator",
  };
  var ADMIN_PAGES = ["/users.html", "/audit.html"];
  var ROLE_ORDER = { viewer: 1, operator: 2, admin: 3 };

  function roleAtLeast(role, minimum) {
    return (ROLE_ORDER[role] || 0) >= (ROLE_ORDER[minimum] || 99);
  }

  function currentPath() {
    var path = window.location.pathname || "/";
    return path === "" ? "/" : path;
  }

  function applyRoleVisibility(role) {
    var nodes = document.querySelectorAll(".module-tree a[href], .app-ribbon a[href]");
    Array.prototype.forEach.call(nodes, function (node) {
      var href = node.getAttribute("href") || "";
      var minimum = PAGE_MIN_ROLE[href];
      if (minimum && !roleAtLeast(role, minimum)) {
        node.style.display = "none";
      }
    });

    var tree = document.querySelector(".module-tree");
    if (tree && roleAtLeast(role, "admin") && !tree.querySelector('a[href="/users.html"]')) {
      var extra = document.createElement("div");
      extra.innerHTML =
        '<a class="tree-node" href="/users.html"><span>USR</span> 账号管理</a>' +
        '<a class="tree-node" href="/audit.html"><span>LOG</span> 审计日志</a>';
      while (extra.firstChild) {
        tree.appendChild(extra.firstChild);
      }
    }

    // 只读账号误入可写页面时，直接引到它有权使用的页面，而不是让它看一堆 403
    var here = currentPath();
    if (PAGE_MIN_ROLE[here] && !roleAtLeast(role, PAGE_MIN_ROLE[here])) {
      window.location.replace("/tables.html");
      return;
    }
    if (ADMIN_PAGES.indexOf(here) >= 0 && !roleAtLeast(role, "admin")) {
      window.location.replace("/tables.html");
    }
  }

  function openPasswordDialog() {
    var existing = document.getElementById("dcPasswordDialog");
    if (existing) {
      existing.remove();
    }
    var dialog = document.createElement("section");
    dialog.className = "dialog";
    dialog.id = "dcPasswordDialog";
    dialog.innerHTML =
      '<div class="dialog-card confirm-card">' +
      '<div class="dialog-title"><strong>修改密码</strong><button type="button" data-close>×</button></div>' +
      '<label class="dc-field">原密码<input type="password" id="dcOldPassword" autocomplete="current-password" /></label>' +
      '<label class="dc-field">新密码<input type="password" id="dcNewPassword" autocomplete="new-password" /></label>' +
      '<label class="dc-field">确认新密码<input type="password" id="dcNewPassword2" autocomplete="new-password" /></label>' +
      '<p id="dcPasswordError" class="dc-field-error"></p>' +
      '<div class="confirm-actions">' +
      '<button type="button" class="button" data-close>取消</button>' +
      '<button type="button" class="primary" id="dcPasswordOk">确认修改</button>' +
      "</div></div>";
    document.body.appendChild(dialog);

    var errorEl = dialog.querySelector("#dcPasswordError");
    function close() {
      dialog.remove();
    }
    Array.prototype.forEach.call(dialog.querySelectorAll("[data-close]"), function (button) {
      button.addEventListener("click", close);
    });
    dialog.querySelector("#dcPasswordOk").addEventListener("click", function () {
      var oldPassword = dialog.querySelector("#dcOldPassword").value;
      var newPassword = dialog.querySelector("#dcNewPassword").value;
      var confirmPassword = dialog.querySelector("#dcNewPassword2").value;
      errorEl.textContent = "";
      if (!oldPassword || !newPassword) {
        errorEl.textContent = "请填写原密码与新密码。";
        return;
      }
      if (newPassword !== confirmPassword) {
        errorEl.textContent = "两次输入的新密码不一致。";
        return;
      }
      rawFetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-DC-Request": "1" },
        body: JSON.stringify({ oldPassword: oldPassword, newPassword: newPassword }),
      })
        .then(function (response) {
          return response.json().then(function (body) {
            return { body: body };
          });
        })
        .then(function (result) {
          if (!result.body || result.body.ok !== true) {
            errorEl.textContent = (result.body && result.body.error) || "修改失败，请重试。";
            return;
          }
          close();
          window.alert("密码已更新，请使用新密码重新登录。");
          window.location.href = "/login.html";
        })
        .catch(function () {
          errorEl.textContent = "网络异常，请稍后重试。";
        });
    });
  }

  function boot() {
    try {
      initAppearance();
    } catch (_) {
      /* 外观开关失败不能影响主流程 */
    }
    initCloseWarning();
    try {
      initAuth();
    } catch (_) {
      /* 登录态探测失败不影响页面本身 */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
