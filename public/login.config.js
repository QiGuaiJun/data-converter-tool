/* ============================================================================
 * 登录页文案配置（2026-09-22）
 *
 * ★ 想改登录页上的文字，**只改这个文件**就行，不用动 login.html / login.js。
 *   改完保存，刷新浏览器即可生效（若没变化按 Ctrl + F5 强刷一次缓存）。
 *
 * 左边是配置项名，右边引号里就是页面上显示的文字。留空字符串 "" 表示不显示那一行。
 * ========================================================================== */
window.LOGIN_CONFIG = {
  /* ---- 左上角品牌 ---- */
  brandCn: "Fe数据导表工具",          // 品牌主标题
  brandEn: "DATA CONVERTER TOOL",     // 品牌英文副标题（会转成大写显示）

  /* ---- 浏览器标签页标题 ---- */
  pageTitle: "登录 - Fe数据导表工具",

  /* ---- 卡片内文字 ---- */
  loginTitle: "",                     // 卡片里的大标题，留空则不显示
  loginSubtitle: "",                  // 大标题下面的说明，留空则不显示
  usernameLabel: "用户名",             // 账号输入框里的提示文字
  passwordLabel: "密码",               // 密码输入框里的提示文字
  loginButton: "登录",                 // 登录按钮文字
  loginButtonBusy: "登录中…",          // 点了登录之后的按钮文字

  /* ---- 注册（切换后的文字）---- */
  signupTitle: "创建新账号",            // 切到注册时显示的大标题
  signupSubtitle: "注册后即可使用本工具",  // 会被服务端返回的角色说明覆盖（见下）
  signupButton: "注册并登录",
  signupButtonBusy: "注册中…",
  signupBackText: "返回登录",
  signupCodeLabel: "邀请码",
  signupCodePlaceholder: "请输入管理员提供的邀请码",

  /* ---- 底部 —— */
  switchPrefix: "还没有账号？",         // 切换链接前面的文字
  switchLinkText: "创建新账号",         // 切换链接文字（登录模式下）
  copyright: "© 2024 Data Converter Tool",
  vision: "项目愿景：让工作简单起来",
  showVersion: true,                  // 是否显示版本号小徽章

  /* ---- 服务端不可达时的兜底提示 ---- */
  networkError: "网络异常，请稍后再试。",
};
