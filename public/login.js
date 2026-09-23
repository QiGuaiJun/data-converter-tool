/* 登录页脚本（2026-09-22 改版）
 *
 * 职责：应用 login.config.js 里的文案、登录 / 注册切换、密码明文切换、按 next 跳回原页面。
 *
 * 两点设计约束：
 *  1) 不依赖 shell.js —— 登录页必须能在「未登录」状态下工作，
 *     而 shell.js 会做登录态检查并跳转，引入会形成跳转回环；
 *  2) 页面上所有文字都来自 window.LOGIN_CONFIG，**本文件里不硬编码任何界面文案**
 *     （除服务端返回的角色说明外），改文字只需要改 login.config.js。
 */
(function () {
  "use strict";

  var FALLBACK = {
    brandCn: "数据导表工具",
    brandEn: "DATA CONVERTER TOOL",
    pageTitle: "登录",
    loginTitle: "",
    loginSubtitle: "",
    usernameLabel: "用户名",
    passwordLabel: "密码",
    loginButton: "登录",
    loginButtonBusy: "登录中…",
    signupTitle: "创建新账号",
    signupSubtitle: "",
    signupButton: "注册并登录",
    signupButtonBusy: "注册中…",
    signupBackText: "返回登录",
    signupCodeLabel: "邀请码",
    signupCodePlaceholder: "请输入邀请码",
    switchPrefix: "还没有账号？",
    switchLinkText: "创建新账号",
    copyright: "",
    vision: "",
    showVersion: true,
    networkError: "网络异常，请稍后再试。",
  };

  var CFG = Object.assign({}, FALLBACK, window.LOGIN_CONFIG || {});
  var $ = function (selector) {
    return document.querySelector(selector);
  };

  var nextTarget = (function () {
    var value = new URLSearchParams(window.location.search).get("next") || "/";
    // 只接受站内路径，避免被构造成开放重定向
    return value.indexOf("/") === 0 && value.indexOf("//") !== 0 ? value : "/";
  })();

  var mode = "login"; // login | signup
  var signupCodeRequired = false;
  // 注册后拿到什么角色由服务端 DC_DEFAULT_ROLE 决定，文案跟着服务端走，
  // 否则运营把默认角色改成「可写」后，登录页还在骗用户说只有查看权限。
  var signupHint = CFG.signupSubtitle;

  function setMessage(text, ok) {
    var box = $("#loginMessage");
    box.textContent = text || "";
    box.className = "ln-message" + (ok ? " ok" : "");
  }

  function setText(selector, value) {
    var node = $(selector);
    if (node) {
      node.textContent = value || "";
    }
  }

  function setField(sectionSelector, placeholder, visible) {
    var holder = $(sectionSelector);
    if (!holder) {
      return;
    }
    var input = holder.querySelector("input");
    if (input) {
      input.setAttribute("placeholder", placeholder || "");
      input.setAttribute("aria-label", placeholder || "");
    }
    holder.classList.toggle("ln-hidden", visible === false);
  }

  /* ------------------------------------------------------------- 文案应用 */
  function applyConfig() {
    document.title = CFG.pageTitle || CFG.brandCn;
    setText("#brandCn", CFG.brandCn);
    setText("#brandEn", String(CFG.brandEn || "").toUpperCase());
    setField("#fieldUsername", CFG.usernameLabel);
    setField("#fieldPassword", CFG.passwordLabel);
    setField("#signupCodeField", CFG.signupCodePlaceholder);
    // 版权里的 {year} 自动替换成当前年份，避免"页面还写着旧年份"这种每年都要修的小问题
    setText("#footCopyright", String(CFG.copyright || "").replace(/\{year\}/g, String(new Date().getFullYear())));
    setText("#footVision", CFG.vision);
    if (CFG.showVersion === false) {
      $("#footVersion").hidden = true;
    }
  }

  /* --------------------------------------------------------- 登录/注册切换 */
  function applyMode(next) {
    mode = next;
    var isSignup = mode === "signup";

    var title = isSignup ? CFG.signupTitle : CFG.loginTitle;
    var subtitle = isSignup ? signupHint : CFG.loginSubtitle;

    setText("#formTitle", title);
    $("#formTitle").classList.toggle("ln-hidden", !title);
    setText("#formSubtitle", subtitle);
    $("#formSubtitle").classList.toggle("ln-hidden", !subtitle);

    $("#signupCodeField").classList.toggle("ln-hidden", !(isSignup && signupCodeRequired));
    $("#loginPassword").setAttribute("autocomplete", isSignup ? "new-password" : "current-password");

    setText("#switchPrefix", isSignup ? "" : CFG.switchPrefix);
    setText("#signupToggle", isSignup ? CFG.signupBackText : CFG.switchLinkText);
    $("#signupToggle").setAttribute("href", "#");

    setBusy(false);
    setMessage("");
  }

  function setBusy(busy) {
    var button = $("#loginSubmit");
    button.disabled = busy;
    button.textContent = busy
      ? (mode === "login" ? CFG.loginButtonBusy : CFG.signupButtonBusy)
      : (mode === "login" ? CFG.loginButton : CFG.signupButton);
  }

  /* --------------------------------------------------------- 密码明文切换 */
  function initPasswordToggle() {
    var input = $("#loginPassword");
    var button = $("#togglePassword");
    if (!input || !button) {
      return;
    }
    button.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      $("#eyeOpen").classList.toggle("ln-hidden", show);
      $("#eyeOff").classList.toggle("ln-hidden", !show);
      button.setAttribute("aria-pressed", show ? "true" : "false");
      var label = show ? "隐藏密码" : "显示密码";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
      // 让光标停在原处，避免切换后又要重新点一下密码框
      input.focus();
      try {
        var end = input.value.length;
        input.setSelectionRange(end, end);
      } catch (_) {
        /* 某些浏览器在 password 类型下不允许 setSelectionRange，忽略即可 */
      }
    });
  }

  /* --------------------------------------------------------------- 提交 */
  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return { ok: false, error: CFG.networkError };
          })
          .then(function (payload) {
            return { ok: response.ok && payload.ok !== false, status: response.status, payload: payload };
          });
      });
  }

  function submit(event) {
    event.preventDefault();
    var username = $("#loginUsername").value.trim();
    var password = $("#loginPassword").value;
    if (!username || !password) {
      setMessage("请填写" + CFG.usernameLabel + "与" + CFG.passwordLabel + "。");
      return;
    }
    setBusy(true);
    setMessage("");

    var request =
      mode === "signup"
        ? postJson("/api/auth/register", {
            username: username,
            password: password,
            code: $("#signupCode").value.trim(),
          })
        : postJson("/api/auth/login", { username: username, password: password });

    request
      .then(function (result) {
        if (!result.ok) {
          setMessage(result.payload.error || CFG.networkError);
          setBusy(false);
          return;
        }
        window.location.href = nextTarget;
      })
      .catch(function (error) {
        setMessage(CFG.networkError + "（" + error.message + "）");
        setBusy(false);
      });
  }

  /* --------------------------------------------------- 注册开放情况与版本 */
  function loadSignupInfo() {
    return fetch("/api/auth/signup-info")
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        var authEnabled = payload.authEnabled !== false;
        signupCodeRequired = Boolean(payload.signupCodeRequired);
        if (payload.defaultRoleHint) {
          signupHint = String(payload.defaultRoleHint);
        }
        $("#signupToggle").classList.toggle("ln-hidden", !authEnabled);
        if (mode === "signup") {
          applyMode("signup");
        }
        var badge = $("#footVersion");
        if (payload.appVersion && CFG.showVersion !== false) {
          badge.textContent = "v" + payload.appVersion;
          badge.hidden = false;
        }
      })
      .catch(function () {
        /* 拿不到信息时不显示注册入口与版本号，不影响登录本身 */
      });
  }

  function boot() {
    applyConfig();
    applyMode("login");
    initPasswordToggle();
    $("#loginForm").addEventListener("submit", submit);
    $("#signupToggle").addEventListener("click", function (event) {
      event.preventDefault();
      applyMode(mode === "login" ? "signup" : "login");
      $("#loginUsername").focus();
    });
    loadSignupInfo();

    // 已登录时（例如手动回到登录页）直接进主界面
    fetch("/api/auth/me")
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(function (payload) {
        if (payload && payload.ok && payload.user) {
          window.location.href = nextTarget;
        }
      })
      .catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
