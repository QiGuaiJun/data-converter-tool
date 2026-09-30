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
    forgotLabel: "忘记密码？",
    forgotHint: "",
    forgotContact: "",
    forgotIdLabel: "账号 / 邮箱 / 手机号",
    forgotSend: "发送验证码",
    forgotSending: "发送中…",
    forgotCodeLabel: "邮箱收到的 6 位验证码",
    forgotPasswordLabel: "新密码（至少 8 位）",
    forgotConfirmLabel: "再输一次新密码",
    forgotReset: "重置密码",
    forgotResetting: "重置中…",
    forgotBack: "返回上一步",
    forgotSent: "验证码已发送",
    forgotPasswordMismatch: "两次输入的新密码不一致。",
    forgotDone: "密码已重置，请用新密码登录。",
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
  // 忘记密码：emailRecovery 由服务端 /api/auth/signup-info 告知（是否配好了发信邮箱），
  // forgotStep 是两步自助流程里的当前步（1 发验证码 / 2 输码改密）。
  var emailRecovery = false;
  var forgotStep = 1;
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
    // 忘记密码：文案全部来自配置，留空则整行不显示
    setText("#forgotToggle", CFG.forgotLabel);
    $("#forgotToggle").classList.toggle("ln-hidden", !CFG.forgotLabel);
    setText("#forgotHint", CFG.forgotHint);
    $("#forgotHint").classList.toggle("ln-hidden", !CFG.forgotHint);
    setText("#forgotContact", CFG.forgotContact);
    $("#forgotContact").classList.toggle("ln-hidden", !CFG.forgotContact);
    // 自助找回表单的输入提示与按钮文字
    setField("#fieldForgotId", CFG.forgotIdLabel);
    setField("#fieldForgotCode", CFG.forgotCodeLabel);
    setField("#fieldForgotPass1", CFG.forgotPasswordLabel);
    setField("#fieldForgotPass2", CFG.forgotConfirmLabel);
    setText("#forgotSendBtn", CFG.forgotSend);
    setText("#forgotResetBtn", CFG.forgotReset);
    setText("#forgotBack", CFG.forgotBack);
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
    // 忘记密码只在登录模式有意义：切到注册时收起入口并折叠面板
    $("#forgotRow").classList.toggle("ln-hidden", isSignup);
    if (isSignup) {
      setForgotOpen(false);
    }
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

  /* --------------------------------------------------------- 忘记密码 */
  // 展开/收起找回说明。
  // 分两种形态（由服务端 /api/auth/signup-info 的 emailRecovery 决定）：
  //   · 配了发信邮箱 → 两步自助：① 发验证码 → ② 输码 + 设新密码；
  //   · 没配 → 只显示"联系管理员重置"的静态说明。
  // 两条路都保留最下面那行管理员联系方式（永远可用的兜底）。
  function setForgotOpen(open) {
    var panel = $("#forgotPanel");
    var button = $("#forgotToggle");
    panel.classList.toggle("ln-hidden", !open);
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      setForgotStep(1);
      setForgotTip("");
    }
  }

  function applyForgotMode() {
    var form = $("#forgotForm");
    if (!form) {
      return;
    }
    form.classList.toggle("ln-hidden", !emailRecovery);
    $("#forgotFallback").classList.toggle("ln-hidden", emailRecovery);
  }

  function setForgotStep(step) {
    forgotStep = step;
    $("#forgotStep1").classList.toggle("ln-hidden", step !== 1);
    $("#forgotStep2").classList.toggle("ln-hidden", step !== 2);
    setForgotTip("");
  }

  function setForgotTip(text, kind) {
    var box = $("#forgotTip");
    if (!box) {
      return;
    }
    box.textContent = text || "";
    box.className = "ln-forgot-line ln-forgot-tip" + (kind ? " is-" + kind : "");
  }

  function setForgotBusy(busy) {
    var send = $("#forgotSendBtn");
    var reset = $("#forgotResetBtn");
    send.disabled = busy;
    reset.disabled = busy;
    send.textContent = busy && forgotStep === 1 ? CFG.forgotSending : CFG.forgotSend;
    reset.textContent = busy && forgotStep === 2 ? CFG.forgotResetting : CFG.forgotReset;
  }

  function sendForgotCode() {
    var identifier = $("#forgotIdentifier").value.trim();
    if (!identifier) {
      setForgotTip("请填写" + CFG.forgotIdLabel + "。", "error");
      return;
    }
    setForgotBusy(true);
    setForgotTip("");
    postJson("/api/auth/forgot/send", { identifier: identifier })
      .then(function (result) {
        setForgotBusy(false);
        if (!result.ok) {
          setForgotTip(result.payload.error || CFG.networkError, "error");
          return;
        }
        setForgotStep(2);
        setForgotTip((result.payload.message || CFG.forgotSent) , "ok");
        $("#forgotCode").focus();
      })
      .catch(function (error) {
        setForgotBusy(false);
        setForgotTip(CFG.networkError + "（" + error.message + "）", "error");
      });
  }

  function resetForgotPassword() {
    var identifier = $("#forgotIdentifier").value.trim();
    var code = $("#forgotCode").value.trim();
    var password = $("#forgotPassword").value;
    var confirm = $("#forgotPassword2").value;
    if (!code) {
      setForgotTip("请填写" + CFG.forgotCodeLabel + "。", "error");
      return;
    }
    if (password !== confirm) {
      setForgotTip(CFG.forgotPasswordMismatch, "error");
      return;
    }
    if (password.length < 8) {
      setForgotTip("新密码至少 8 位。", "error");
      return;
    }
    setForgotBusy(true);
    setForgotTip("");
    postJson("/api/auth/forgot/reset", { identifier: identifier, code: code, password: password })
      .then(function (result) {
        setForgotBusy(false);
        if (!result.ok) {
          setForgotTip(result.payload.error || CFG.networkError, "error");
          return;
        }
        // 重置成功：收起找回面板、切回登录，并把标识原样回填（账号名/邮箱/手机号
        // 现在都能用于登录，回填省得再打一遍），密码留空等用户输入新密码
        setForgotOpen(false);
        applyMode("login");
        $("#loginUsername").value = identifier;
        setMessage(result.payload.message || CFG.forgotDone, true);
        $("#loginPassword").value = "";
        $("#loginPassword").focus();
      })
      .catch(function (error) {
        setForgotBusy(false);
        setForgotTip(CFG.networkError + "（" + error.message + "）", "error");
      });
  }

  function initForgot() {
    var button = $("#forgotToggle");
    if (!button) {
      return;
    }
    button.addEventListener("click", function (event) {
      event.preventDefault(); // 按钮在 <form> 里，必须阻止默认提交
      setForgotOpen($("#forgotPanel").classList.contains("ln-hidden"));
    });
    applyForgotMode();
    $("#forgotSendBtn").addEventListener("click", function (event) {
      event.preventDefault();
      sendForgotCode();
    });
    $("#forgotResetBtn").addEventListener("click", function (event) {
      event.preventDefault();
      resetForgotPassword();
    });
    $("#forgotBack").addEventListener("click", function (event) {
      event.preventDefault();
      setForgotStep(1);
    });
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
        // 服务端配好了发信邮箱才展示"邮箱自助找回"，否则退回"联系管理员"的静态说明，
        // 避免用户点了发送却永远收不到邮件。
        emailRecovery = payload.emailRecovery === true;
        applyForgotMode();
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
    initForgot();
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
