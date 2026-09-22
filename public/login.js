/* 登录页脚本（P1，2026-09-22）
 *
 * 职责：登录 / 注册、错误提示、按 next 参数跳回原页面。
 * 不依赖 shell.js —— 登录页必须能在"未登录"状态下正常工作，
 * 而 shell.js 会做登录态检查并跳转，引入会形成回环。
 */
(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const nextTarget = (() => {
    const value = new URLSearchParams(window.location.search).get("next") || "/";
    // 只接受站内路径，避免被构造成开放重定向
    return value.startsWith("/") && !value.startsWith("//") ? value : "/";
  })();

  let mode = "login"; // login | signup
  let signupCodeRequired = false;

  function setMessage(text, ok) {
    const box = $("#loginMessage");
    box.textContent = text || "";
    box.className = "login-message" + (ok ? " ok" : "");
  }

  function setBusy(busy) {
    $("#loginSubmit").disabled = busy;
    $("#loginSubmit").textContent = busy
      ? (mode === "login" ? "登录中…" : "注册中…")
      : (mode === "login" ? "登录" : "注册并登录");
  }

  function applyMode(next) {
    mode = next;
    const isSignup = mode === "signup";
    $("#signupToggle").textContent = isSignup ? "返回登录" : "注册新账号";
    $("#signupCodeField").classList.toggle("login-hidden", !(isSignup && signupCodeRequired));
    $("#signupDisplayField").classList.toggle("login-hidden", !isSignup);
    $("#loginPassword").setAttribute("autocomplete", isSignup ? "new-password" : "current-password");
    $("#loginSubtitle").textContent = isSignup
      ? "注册后默认只有查看权限（viewer），如需写入请让管理员调整角色"
      : "请登录后使用（本工具含数据库写入能力，仅限授权人员）";
    setMessage("");
    setBusy(false);
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_) {
      payload = { ok: false, error: "服务返回异常，请稍后再试。" };
    }
    return { ok: response.ok && payload.ok !== false, status: response.status, payload };
  }

  async function submitLogin(event) {
    event.preventDefault();
    const username = $("#loginUsername").value.trim();
    const password = $("#loginPassword").value;
    if (!username || !password) {
      setMessage("请填写用户名与密码。");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      if (mode === "signup") {
        const result = await postJson("/api/auth/register", {
          username,
          password,
          displayName: $("#signupDisplay").value.trim(),
          code: $("#signupCode").value.trim(),
        });
        if (!result.ok) {
          setMessage(result.payload.error || "注册失败，请稍后再试。");
          setBusy(false);
          return;
        }
        window.location.href = nextTarget;
        return;
      }
      const result = await postJson("/api/auth/login", {
        username,
        password,
        remember: $("#loginRemember").checked,
      });
      if (!result.ok) {
        setMessage(result.payload.error || "登录失败，请检查用户名与密码。");
        setBusy(false);
        return;
      }
      window.location.href = nextTarget;
    } catch (error) {
      setMessage("网络异常：" + error.message);
      setBusy(false);
    }
  }

  async function loadSignupInfo() {
    try {
      const response = await fetch("/api/auth/signup-info");
      const payload = await response.json();
      const authEnabled = payload.authEnabled !== false;
      signupCodeRequired = Boolean(payload.signupCodeRequired);
      $("#signupToggle").classList.toggle("login-hidden", !authEnabled);
      $("#loginFoot").textContent = signupCodeRequired
        ? "本工具对外开放注册，但需要管理员提供的邀请码。注册账号默认只能查看。"
        : "本工具对外开放注册，注册后默认为「只能查看」，如需写入请让管理员调整角色。";
      if (payload.appVersion) {
        $("#loginFoot").textContent += `　当前版本 ${payload.appVersion}`;
      }
    } catch (_) {
      /* 拿不到信息时不显示注册入口，不影响登录 */
    }
  }

  $("#loginForm").addEventListener("submit", submitLogin);
  $("#signupToggle").addEventListener("click", () => applyMode(mode === "login" ? "signup" : "login"));

  applyMode("login");
  loadSignupInfo();

  // 如果已经登录（例如手动回到登录页），直接进主界面
  fetch("/api/auth/me")
    .then((response) => (response.ok ? response.json() : null))
    .then((payload) => {
      if (payload && payload.ok && payload.user) {
        window.location.href = nextTarget;
      }
    })
    .catch(() => {});
})();
