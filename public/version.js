// P3-28：应用版本号展示。
// 从 /api/meta 接口获取应用版本号，更新侧边栏底部版本信息。
// 接口异常时保持默认文案，不影响页面其他功能。
(async function loadAppVersion() {
  const target = document.querySelector("#appVersion");
  if (!target) return;
  try {
    const response = await fetch("/api/meta", { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    if (payload && payload.ok && payload.appVersion) {
      target.textContent = `数据导表工具 v${payload.appVersion}`;
    }
  } catch (error) {
    // 网络或服务异常时保留默认文案
  }
})();
