/* 全站共享的页面级行为，所有 HTML 页面统一引入。
 *
 * 1) 改进G「素色模式」：关闭背景壁纸改用纯色画布，选择持久化到 localStorage。
 *    默认仍是壁纸（保持与历史外观一致），只是把开关交到用户手里。
 * 2) 改进E「关闭提醒」：有启用中的定时任务时，关闭/离开页面给出提示，
 *    避免用户以为「关掉页面任务还在跑」。站内跳转不提示（见 internalNavigation）。
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

  function boot() {
    try {
      initAppearance();
    } catch (_) {
      /* 外观开关失败不能影响主流程 */
    }
    initCloseWarning();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
