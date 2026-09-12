/* ==========================================================================
   connections.js —— 「新建连接」页面（2026-09-11 重设计）
   原型交互 + 真实接口：
   - GET    /api/connections            列表
   - POST   /api/connections            保存（带 id 为更新；密码留空沿用已存密码）
   - DELETE /api/connections?id=        删除
   - POST   /api/connections/test       测试连接（返回 version + databases）
   约定：
   - 仅 MySQL 可真实测试/保存；SQLite 为本工具内置本地库（提示无需新建）；
     PostgreSQL / SQL Server / Oracle / ClickHouse 显示"待开发"并禁用按钮。
   - 「常用参数」6 项目前仅视觉展示，后端无对应字段，不随保存提交。
   - 状态列真实探测：列表加载后对每条连接并发调用 /api/connections/test
     （只读：select version() / show databases），探测中显示「检测中…」，
     结果落「已连接 / 未连接」；页面停留期间每 PROBE_INTERVAL_MS 自动重探一轮。
     失败的连接把 MySQL 错误码翻译成中文，挂在 pill 的 title / aria-label 上。
   ========================================================================== */

(function () {
  'use strict';

  /* ====================== 数据库类型注册表 ====================== */
  // supported=true 表示可真实测试/保存；builtin=true 表示工具内置库（无需新建）。
  var DB_TYPES = [
    { id: 'mysql',      label: 'MySQL / MariaDB', short: 'MySQL',      port: 3306, version: '5.6+',  supported: true,  builtin: false },
    { id: 'postgresql', label: 'PostgreSQL',      short: 'PostgreSQL', port: 5432, version: '9.6+',  supported: false, builtin: false },
    { id: 'sqlserver',  label: 'SQL Server',      short: 'SQL Server', port: 1433, version: '2012+', supported: false, builtin: false },
    { id: 'oracle',     label: 'Oracle',          short: 'Oracle',     port: 1521, version: '11g+',  supported: false, builtin: false },
    { id: 'sqlite',     label: 'SQLite',          short: 'SQLite',     port: null, version: '3.x',   supported: false, builtin: true  },
    { id: 'clickhouse', label: 'ClickHouse',      short: 'ClickHouse', port: 9000, version: '21.x+', supported: false, builtin: false }
  ];

  var UNSUPPORTED_TEXT = '该数据库类型功能待开发中，当前仅支持 MySQL';
  var SQLITE_TEXT = 'SQLite 是本工具的内置本地库，无需新建连接';

  /* ====================== 状态探测参数 ====================== */
  // 自动重探间隔（毫秒）；页面隐藏时暂停，重新可见时立即补探一次。
  var PROBE_INTERVAL_MS = 30000;
  // 无法归类的错误原文截断长度（避免 title 过长）。
  var PROBE_REASON_MAX = 120;
  // 非 MySQL 类型不探测，直接给「暂不支持」结论。
  var PROBE_UNSUPPORTED_REASON = '该类型暂不支持检测';

  // MySQL 错误码 → 中文人话（错误原文形如 (2003, "Can't connect to ...")）。
  var PROBE_ERROR_TEXT = {
    '2003': '连接超时或主机不可达',
    '1045': '用户名或密码错误',
    '1049': '数据库不存在'
  };

  /* ====================== 图标（手写 inline SVG） ====================== */
  var DB_ICON = {
    mysql:
      '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><path fill="#00618a" d="M16.8 3.2c-.7 2.1-2.1 3.6-4.1 4.5.6-1.4.8-2.7.6-3.9-1.6 1.1-2.5 2.7-2.8 4.7-1.1-1.3-2.7-2.1-4.7-2.4.9.7 1.5 1.5 1.9 2.5-1.4-.4-2.8-.2-4.1.7 1.3.1 2.5.5 3.5 1.2-1.4 1-2.2 2.4-2.4 4 1.3-1.1 2.9-1.8 4.7-2 3.5-.4 6.1-2.6 7.2-6 .2-.8.3-1.6.2-2.3-.5.2-1 .5-1.4.9z"/></svg>',
    postgresql:
      '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><path fill="#336791" d="M9 1.3c-3.6 0-5.7 1.9-5.7 4.7 0 1.5.2 3 .5 4.3.3 1.2 1.2 1.6 1.9 1.2.3 1.1.9 2 1.9 2.6.3.2.7 0 .7-.4v-2.2c.4.1.9.1 1.4 0v2.2c0 .4.4.6.7.4 1-.6 1.6-1.5 1.9-2.6.7.4 1.6 0 1.9-1.2.3-1.3.5-2.8.5-4.3 0-2.8-2.1-4.7-5.7-4.7z"/><circle cx="6.6" cy="5.7" r=".95" fill="#fff"/><circle cx="11.4" cy="5.7" r=".95" fill="#fff"/></svg>',
    sqlserver:
      '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><g fill="#a91e22"><ellipse cx="9" cy="4" rx="6" ry="2.4"/><path d="M3 4v4.2c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4V4c0 1.3-2.7 2.4-6 2.4S3 5.3 3 4z"/><path d="M3 9.2v4.2c0 1.3 2.7 2.4 6 2.4s6-1.1 6-2.4V9.2c0 1.3-2.7 2.4-6 2.4S3 10.5 3 9.2z"/></g></svg>',
    oracle:
      '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><ellipse cx="9" cy="9" rx="8" ry="5.2" fill="none" stroke="#ea1b22" stroke-width="2.4"/></svg>',
    sqlite:
      '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><path fill="#0f80cc" d="M15.5 2.5c-4.5.3-8 2.2-9.9 5.5-1 1.7-1.4 3.6-1.2 5.4l-1.9 1.9c-.3.3-.3.8 0 1.1.3.3.8.3 1.1 0l1.9-1.9c1.9.2 3.8-.3 5.4-1.3 3.3-1.9 5.2-5.4 5.5-9.9 0-.5-.4-.9-.9-.8z"/><path d="M11.7 6.1 5.4 12.4" stroke="#fff" stroke-width="1.1" stroke-linecap="round"/></svg>',
    clickhouse:
      '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><g fill="#ffcc01"><rect x="1.1" y="10.2" width="2.1" height="6.4"/><rect x="4.7" y="7.2" width="2.1" height="9.4"/><rect x="8.3" y="4.2" width="2.1" height="12.4"/><rect x="11.9" y="1.8" width="2.1" height="14.8"/><rect x="15.5" y="1.8" width="1.6" height="14.8"/></g></svg>'
  };

  var GUIDE_ICON = {
    host: '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><rect x="2" y="3" width="12" height="7.4" rx="1.4"/><path d="M6 13.2h4M8 10.4v2.8" stroke-linecap="round"/></svg>',
    port: '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><circle cx="8" cy="8" r="5.6"/><path d="M2.4 8h11.2M8 2.4c2.5 3 2.5 8.2 0 11.2M8 2.4c-2.5 3-2.5 8.2 0 11.2"/></svg>',
    user: '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><circle cx="8" cy="5.4" r="2.7"/><path d="M3.1 13.6c.8-2.7 2.7-4.1 4.9-4.1s4.1 1.4 4.9 4.1" stroke-linecap="round"/></svg>',
    database: '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><ellipse cx="8" cy="4.2" rx="5" ry="2"/><path d="M3 4.2v7.6c0 1.1 2.2 2 5 2s5-.9 5-2V4.2"/><path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/></svg>',
    charset: '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><rect x="2.2" y="2.2" width="11.6" height="11.6" rx="2.4"/><text x="8" y="11" text-anchor="middle" font-size="7.2" font-weight="700" fill="currentColor" stroke="none" font-family="Arial, sans-serif">Aa</text></svg>'
  };

  var UI = {
    check: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="M5.4 8.2 7.1 9.9l3.5-3.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    error: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="M8 4.6v4.4" stroke-linecap="round"/><circle cx="8" cy="11.4" r=".85" fill="currentColor" stroke="none"/></svg>',
    info: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><circle cx="8" cy="8" r="6.4"/><path d="M8 7.4v4.2" stroke-linecap="round"/><circle cx="8" cy="4.9" r=".85" fill="currentColor" stroke="none"/></svg>',
    warning: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M8 2.2 14.4 13.4H1.6z" stroke-linejoin="round"/><path d="M8 6.6v3.2" stroke-linecap="round"/><circle cx="8" cy="11.6" r=".8" fill="currentColor" stroke="none"/></svg>',
    eye: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M1.6 8S4.1 3.9 8 3.9 14.4 8 14.4 8 11.9 12.1 8 12.1 1.6 8 1.6 8z"/><circle cx="8" cy="8" r="2.1"/></svg>',
    eyeOff: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M1.6 8S4.1 3.9 8 3.9 14.4 8 14.4 8 11.9 12.1 8 12.1 1.6 8 1.6 8z"/><circle cx="8" cy="8" r="2.1"/><path d="M2.6 2.6 13.4 13.4" stroke-linecap="round"/></svg>',
    pencil: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><path d="M11.1 2.3l2.6 2.6-8 8-3.3.7.7-3.3z" stroke-linejoin="round"/></svg>',
    copy: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><rect x="5.6" y="1.9" width="8.5" height="10.2" rx="1.4"/><path d="M10.6 13.2H3.5a1.6 1.6 0 0 1-1.6-1.6V1.9"/></svg>',
    trash: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><path d="M2.6 4.3h10.8M6.3 4.3V2.9c0-.5.4-.9.9-.9h1.6c.5 0 .9.4.9.9v1.4M4.2 4.3l.6 8.4c.05.6.55 1.1 1.15 1.1h4.1c.6 0 1.1-.5 1.15-1.1l.6-8.4" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  };

  /* ====================== 各类型「连接说明」 ====================== */
  var DB_GUIDE = {
    mysql: { title: 'MySQL 连接说明', items: [
      { icon: 'host',     title: '主机地址',     text: '输入数据库服务器的 IP 地址或域名' },
      { icon: 'port',     title: '端口',         text: 'MySQL 默认端口为 3306' },
      { icon: 'user',     title: '用户名和密码', text: '输入具有访问权限的数据库账号' },
      { icon: 'database', title: '数据库',       text: '可选，留空可在连接后手动选择' },
      { icon: 'charset',  title: '字符集建议',   text: '推荐使用 utf8mb4 以支持完整的 Unicode 字符' }
    ]},
    sqlite: { title: 'SQLite 连接说明', items: [
      { icon: 'host',     title: '内置本地库',   text: 'SQLite 由工具内置管理，直接在任务中选择即可' },
      { icon: 'port',     title: '端口',         text: 'SQLite 为本地文件数据库，无端口' },
      { icon: 'user',     title: '用户名和密码', text: '本地文件访问，无需账号密码' },
      { icon: 'database', title: '数据库文件',   text: '由工具自动管理，无需手动填写路径' },
      { icon: 'charset',  title: '字符集建议',   text: '推荐 UTF-8 编码，避免中文乱码' }
    ]},
    postgresql: { title: 'PostgreSQL 连接说明', items: [
      { icon: 'host',     title: '主机地址',     text: '输入 PostgreSQL 服务器的 IP 地址或域名' },
      { icon: 'port',     title: '端口',         text: 'PostgreSQL 默认端口为 5432' },
      { icon: 'user',     title: '用户名和密码', text: '输入具有访问权限的数据库账号' },
      { icon: 'database', title: '数据库',       text: '可选，留空可在连接后手动选择' },
      { icon: 'charset',  title: '字符集建议',   text: '推荐 UTF8 编码，与服务器端保持一致' }
    ]},
    sqlserver: { title: 'SQL Server 连接说明', items: [
      { icon: 'host',     title: '主机地址',     text: '输入 SQL Server 实例的 IP 地址或主机名' },
      { icon: 'port',     title: '端口',         text: 'SQL Server 默认端口为 1433' },
      { icon: 'user',     title: '用户名和密码', text: '支持 SQL 账号或 Windows 身份验证账号' },
      { icon: 'database', title: '数据库',       text: '可选，留空可在连接后手动选择' },
      { icon: 'charset',  title: '字符集建议',   text: '一般使用默认排序规则，无需单独设置' }
    ]},
    oracle: { title: 'Oracle 连接说明', items: [
      { icon: 'host',     title: '主机地址',     text: '输入 Oracle 服务器的 IP 地址或主机名' },
      { icon: 'port',     title: '端口',         text: 'Oracle 默认监听端口为 1521' },
      { icon: 'user',     title: '用户名和密码', text: '输入具有访问权限的数据库账号' },
      { icon: 'database', title: '数据库',       text: '填写服务名（SID 或 Service Name）' },
      { icon: 'charset',  title: '字符集建议',   text: '建议与数据库服务器字符集保持一致' }
    ]},
    clickhouse: { title: 'ClickHouse 连接说明', items: [
      { icon: 'host',     title: '主机地址',     text: '输入 ClickHouse 服务器的 IP 地址或域名' },
      { icon: 'port',     title: '端口',         text: 'ClickHouse 默认端口为 9000' },
      { icon: 'user',     title: '用户名和密码', text: '默认账号为 default，请按需配置密码' },
      { icon: 'database', title: '数据库',       text: '可选，默认使用 default 数据库' },
      { icon: 'charset',  title: '字符集建议',   text: '推荐 UTF-8 编码以支持完整 Unicode' }
    ]}
  };

  /* ====================== 状态 ====================== */
  var state = {
    connections: [],        // 服务端返回的真实连接列表
    typeId: 'mysql',
    editingId: '',          // 正在编辑的连接 id（空 = 新建）
    editingItem: null,      // 正在编辑的原始行（用于 ssl 字段透传）
    formTested: false,      // 当前表单是否测试成功（保存后给该行「已连接」）
    // 状态探测结果：id → { status, reason, at, version, databasesCount }
    // status ∈ 'checking'（探测中）| 'ok'（已连接）| 'fail'（未连接）| 'unsupported'（暂不支持）
    probe: {},
    pending: 0,             // 本轮 in-flight 探测请求数（>0 时定时器跳过本轮）
    lastAt: 0,              // 最近一轮探测完成时间戳（0 = 从未探测）
    timer: 0,               // 定时器句柄（0 = 未启动）
    keyword: '',
    typeFilter: 'all',
    saving: false
  };

  /* ====================== 工具 ====================== */
  function $(selector) { return document.querySelector(selector); }
  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function typeMeta(id) {
    for (var i = 0; i < DB_TYPES.length; i++) {
      if (DB_TYPES[i].id === id) return DB_TYPES[i];
    }
    return DB_TYPES[0];
  }

  function findConnection(id) {
    for (var i = 0; i < state.connections.length; i++) {
      if (state.connections[i].id === id) return state.connections[i];
    }
    return null;
  }

  async function requestJson(url, options) {
    options = options || {};
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || '请求失败。');
    }
    return payload;
  }

  /* ====================== Toast / 弹窗 ====================== */
  function showToast(kind, text) {
    var stack = byId('connToastStack');
    var el = document.createElement('div');
    el.className = 'conn-toast ' + kind;
    el.innerHTML = (UI[kind] || UI.info) + '<span>' + escapeHtml(text) + '</span>';
    stack.appendChild(el);
    window.setTimeout(function () {
      el.classList.add('leaving');
      window.setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 200);
    }, 2600);
  }

  function confirmDialog(title, text, okText) {
    return new Promise(function (resolve) {
      var mask = byId('connConfirmMask');
      byId('connConfirmTitle').textContent = title;
      byId('connConfirmText').textContent = text;
      byId('connConfirmOk').textContent = okText || '确定';
      mask.classList.remove('hidden');

      function cleanup(result) {
        mask.classList.add('hidden');
        byId('connConfirmOk').removeEventListener('click', onOk);
        byId('connConfirmCancel').removeEventListener('click', onCancel);
        mask.removeEventListener('click', onMask);
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }
      function onOk() { cleanup(true); }
      function onCancel() { cleanup(false); }
      function onMask(e) { if (e.target === mask) cleanup(false); }
      function onKey(e) { if (e.key === 'Escape') cleanup(false); }

      byId('connConfirmOk').addEventListener('click', onOk);
      byId('connConfirmCancel').addEventListener('click', onCancel);
      mask.addEventListener('click', onMask);
      document.addEventListener('keydown', onKey);
    });
  }

  /* ====================== 渲染：右栏 / 说明卡 / 下拉 ====================== */
  function renderDbTypeList() {
    byId('connDbTypeList').innerHTML = DB_TYPES.map(function (t) {
      return '<div class="conn-db-type-item">' + (DB_ICON[t.id] || '') +
        '<span class="conn-db-type-name">' + escapeHtml(t.label) + '</span>' +
        '<span class="conn-db-type-version">' + escapeHtml(t.version) + '</span>' +
        '</div>';
    }).join('');
  }

  function renderGuide(meta) {
    var guide = DB_GUIDE[meta.id] || DB_GUIDE.mysql;
    byId('connGuideTitle').textContent = guide.title;
    byId('connGuideList').innerHTML = guide.items.map(function (item) {
      return '<div class="conn-guide-item">' +
        '<span class="conn-guide-icon">' + (GUIDE_ICON[item.icon] || GUIDE_ICON.host) + '</span>' +
        '<div class="conn-guide-body">' +
          '<div class="conn-guide-item-title">' + escapeHtml(item.title) + '</div>' +
          '<p class="conn-guide-text">' + escapeHtml(item.text) + '</p>' +
        '</div>' +
        '</div>';
    }).join('');
  }

  function renderDbMenu() {
    byId('connDbMenu').innerHTML = DB_TYPES.map(function (t) {
      return '<li class="conn-db-option" role="option" data-type="' + t.id + '" aria-selected="' +
        (t.id === state.typeId) + '">' + (DB_ICON[t.id] || '') +
        '<span>' + escapeHtml(t.label) + '</span>' +
        (t.id === state.typeId ? '<span class="conn-db-option-check">' + UI.check + '</span>' : '') +
        '</li>';
    }).join('');
  }

  function openDbMenu(open) {
    byId('connDbMenu').classList.toggle('hidden', !open);
    byId('connDbSelect').classList.toggle('open', open);
    byId('connDbTrigger').setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  /* ====================== 渲染：连接列表 ====================== */
  function filteredConnections() {
    var kw = state.keyword.trim().toLowerCase();
    return state.connections.filter(function (c) {
      if (state.typeFilter !== 'all' && c.dbType !== state.typeFilter) return false;
      if (!kw) return true;
      var haystack = [c.name, c.host, c.database, c.user].join(' ').toLowerCase();
      return haystack.indexOf(kw) !== -1;
    });
  }

  /* ====================== 状态探测引擎 ====================== */
  /** 时间戳 → 'HH:MM:SS'（本地时间）。 */
  function formatClock(ts) {
    var d = new Date(ts);
    function pad(n) { return n < 10 ? '0' + n : String(n); }
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  /**
   * 把服务端错误文案翻译成中文人话。
   * 覆盖三层失败：MySQL 错误码（2003/1045/1049）→ 网络层失败 → 响应非 JSON；
   * 其它错误保留原文并按 PROBE_REASON_MAX 截断。
   * @param {string} raw 例如 (2003, "Can't connect to MySQL server on '198.18.0.1' (timed out)")
   * @returns {string} 中文原因
   */
  function probeReasonText(raw) {
    var text = String(raw == null ? '' : raw).trim();
    if (!text) return '连接失败，原因未知';
    var matched = /^\(?\s*(\d{3,5})\b/.exec(text) || /\((\d{3,5})\s*,/.exec(text);
    if (matched && PROBE_ERROR_TEXT[matched[1]]) return PROBE_ERROR_TEXT[matched[1]];
    // 网络层失败：fetch 抛 TypeError（'Failed to fetch'）、请求被 abort、连接被拒等
    if (/Failed to fetch|NetworkError|Network request failed|Load failed|AbortError|aborted|net::ERR_|ERR_CONNECTION|TypeError/i.test(text)) {
      return '无法连接到本机服务，请检查服务是否在运行';
    }
    // 响应体不是合法 JSON：例如 500 返回 HTML 错误页，response.json() 抛 'Unexpected token < ...'
    if (/Unexpected token|Unexpected end of JSON|is not valid JSON|JSON\.parse|SyntaxError/i.test(text)) {
      return '服务返回了非预期响应';
    }
    if (text.length > PROBE_REASON_MAX) return text.slice(0, PROBE_REASON_MAX) + '…';
    return text;
  }

  /**
   * 探测开始前重置探测表：可探测的置「检测中」，非 MySQL 直接置「暂不支持」。
   * 整体重建也顺带丢弃已删除连接的旧结果。
   */
  function markProbeTargets() {
    var now = Date.now();
    var map = {};
    state.connections.forEach(function (c) {
      if (typeMeta(c.dbType).supported) {
        map[c.id] = { status: 'checking', reason: '', at: 0, version: '', databasesCount: 0 };
      } else {
        map[c.id] = { status: 'unsupported', reason: PROBE_UNSUPPORTED_REASON, at: now, version: '', databasesCount: 0 };
      }
    });
    state.probe = map;
  }

  /**
   * 探测单条连接（只读接口：服务端执行 select version() / show databases）。
   * 请求体必须带完整字段 + 空密码：服务端仅用 id 取已存密码，其余字段由前端提供。
   * @param {Object} item 连接列表行
   * @returns {Promise<void>} 永不 reject（失败结果记入 reason）
   */
  function probeConnection(item) {
    var payload = {
      id: item.id,
      name: item.name || '',
      dbType: item.dbType || 'mysql',
      host: item.host || '',
      port: item.port || '',
      user: item.user || '',
      password: '',
      database: item.database || '',
      charset: item.charset || 'utf8mb4'
    };
    state.pending += 1;
    return requestJson('/api/connections/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (result) {
      state.probe[item.id] = {
        status: 'ok',
        reason: '',
        at: Date.now(),
        version: result.version || '',
        databasesCount: (result.databases || []).length
      };
    }).catch(function (error) {
      state.probe[item.id] = {
        status: 'fail',
        reason: probeReasonText(error && error.message),
        at: Date.now(),
        version: '',
        databasesCount: 0
      };
    }).then(function () {
      state.pending -= 1;
      if (state.pending <= 0) {
        state.pending = 0;
        state.lastAt = Date.now();
      }
      updateRowStatus(item.id);
      renderProbeHint();
    });
  }

  /** 并发探测当前列表中所有可探测的连接。 */
  function fireProbes() {
    var jobs = state.connections
      .filter(function (c) { return typeMeta(c.dbType).supported; })
      .map(function (c) { return probeConnection(c); });
    return Promise.all(jobs);
  }

  /**
   * 探测一轮：先把全部行置「检测中」并刷新 UI，再并发发请求（最长 6 秒不能卡住 UI）。
   * 注意顺序：renderProbeHint() 必须在 fireProbes() **之后**调用 —— fireProbes 会
   * 同步把 state.pending 加上去，hint 才能立刻显示「正在检测…」；若放在之前，
   * pending 还是 0，hint 会先落到空串或上一轮的旧时间，且这个错位窗口等于
   * 整轮探测耗时（全部连接不可达时长达 connect_timeout=6 秒）。
   * @param {boolean} force 定时器路径传 false —— 上一轮未结束（有 in-flight 请求）时跳过本轮，避免堆叠；
   *                        用户主动触发的路径（打开页面 / 刷新状态）传 true。
   * @returns {Promise<boolean>} 是否真正发起了本轮探测
   */
  function probeAll(force) {
    if (!force && state.pending > 0) return Promise.resolve(false);
    markProbeTargets();
    renderTable();
    var round = fireProbes();
    renderProbeHint();
    return round.then(function () { return true; });
  }

  /** 只重绘某一行的状态格：保留滚动位置，也不打断用户正在做的事。 */
  function updateRowStatus(id) {
    var rows = byId('connTbody').querySelectorAll('tr[data-id]');
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].getAttribute('data-id') === id) {
        var cell = rows[i].querySelector('.conn-col-status');
        if (cell) cell.innerHTML = statusPill(id);
        return;
      }
    }
  }

  /** 列表标题同一行的灰字提示：探测中 → 「正在检测…」，否则「最后检测 HH:MM:SS」，从未探测留空。 */
  function renderProbeHint() {
    var el = byId('connProbeHint');
    if (!el) return;
    if (state.pending > 0) {
      el.textContent = '正在检测…';
    } else if (state.lastAt) {
      el.textContent = '最后检测 ' + formatClock(state.lastAt);
    } else {
      el.textContent = '';
    }
  }

  /** 启动每 PROBE_INTERVAL_MS 一轮的自动重探；页面隐藏时不探测。 */
  function scheduleProbe() {
    if (state.timer) window.clearInterval(state.timer);
    state.timer = window.setInterval(function () {
      if (document.hidden) return;
      probeAll(false);
    }, PROBE_INTERVAL_MS);
  }

  /** 状态 pill：依据最近一次探测结果渲染，失败/未支持把中文原因挂在 title + aria-label 上。 */
  function statusPill(id) {
    var record = state.probe[id] || null;
    var status = record ? record.status : 'unknown';

    if (status === 'checking') {
      return '<span class="conn-pill conn-pill-checking" title="正在检测连接状态…" aria-label="正在检测连接状态">检测中…</span>';
    }
    if (status === 'ok') {
      var detail = 'MySQL ' + (record.version || '');
      if (record.databasesCount) detail += '，可见 ' + record.databasesCount + ' 个数据库';
      detail += ' · 检测时间 ' + formatClock(record.at);
      return '<span class="conn-pill conn-pill-connected" title="' + escapeHtml(detail) +
        '" aria-label="已连接：' + escapeHtml(detail) + '">已连接</span>';
    }
    if (status === 'fail') {
      var reason = record.reason || '连接失败，原因未知';
      return '<span class="conn-pill conn-pill-disconnected" title="' + escapeHtml(reason) +
        '" aria-label="未连接：' + escapeHtml(reason) + '">未连接</span>';
    }
    if (status === 'unsupported') {
      return '<span class="conn-pill conn-pill-unsupported" title="' + escapeHtml(PROBE_UNSUPPORTED_REASON) +
        '" aria-label="' + escapeHtml(PROBE_UNSUPPORTED_REASON) + '">未检测</span>';
    }
    return '<span class="conn-pill conn-pill-disconnected">未检测</span>';
  }

  function renderTable() {
    var tbody = byId('connTbody');
    var list = filteredConnections();

    if (!list.length) {
      tbody.innerHTML = '<tr><td class="conn-empty-cell" colspan="9">' +
        (state.connections.length ? '暂无匹配的连接' : '暂无连接，先在上方创建一个 MySQL 连接') +
        '</td></tr>';
      return;
    }

    tbody.innerHTML = list.map(function (c, i) {
      var meta = typeMeta(c.dbType);
      var host = c.host ? c.host + (c.port ? ':' + c.port : '') : '—';
      return '<tr data-id="' + escapeHtml(c.id) + '">' +
        '<td class="conn-col-index">' + (i + 1) + '</td>' +
        '<td class="conn-col-name">' + escapeHtml(c.name) + '</td>' +
        '<td class="conn-col-type"><span class="conn-type-cell">' + (DB_ICON[c.dbType] || '') + '<span>' + escapeHtml(meta.short) + '</span></span></td>' +
        '<td class="conn-col-host">' + escapeHtml(host) + '</td>' +
        '<td class="conn-col-db conn-mono">' + (c.database ? escapeHtml(c.database) : '—') + '</td>' +
        '<td class="conn-col-user">' + (c.user ? escapeHtml(c.user) : '—') + '</td>' +
        '<td class="conn-col-status">' + statusPill(c.id) + '</td>' +
        '<td class="conn-col-time">' + escapeHtml(c.updatedAt || '') + '</td>' +
        '<td class="conn-col-ops"><div class="conn-ops">' +
          '<button type="button" class="conn-op-btn" data-action="edit" title="编辑" aria-label="编辑">' + UI.pencil + '</button>' +
          '<button type="button" class="conn-op-btn" data-action="copy" title="复制" aria-label="复制">' + UI.copy + '</button>' +
          '<button type="button" class="conn-op-btn conn-op-delete" data-action="delete" title="删除" aria-label="删除">' + UI.trash + '</button>' +
        '</div></td>' +
        '</tr>';
    }).join('');
  }

  function renderTypeFilter() {
    var sel = byId('connListTypeFilter');
    sel.innerHTML = '<option value="all">全部类型</option>' + DB_TYPES.map(function (t) {
      return '<option value="' + t.id + '">' + escapeHtml(t.short) + '</option>';
    }).join('');
    sel.value = state.typeFilter;
  }

  /* ====================== 数据加载 ====================== */
  /**
   * 拉取连接列表并触发一轮状态探测。
   * 不 await 探测结果：单条不可达连接要等 6 秒，不能让调用方（保存/删除/刷新）卡住；
   * 探测结果回来会通过 updateRowStatus / renderProbeHint 自己更新 UI。
   */
  async function loadConnections() {
    const payload = await requestJson('/api/connections');
    state.connections = payload.connections || [];
    probeAll(true).catch(function () { /* 探测失败已在行内体现，不打断列表刷新 */ });
  }

  /* ====================== 类型联动 ====================== */
  var LOCAL_LOCKED_FIELDS = ['connHost', 'connPort', 'connUser', 'connPassword'];

  function updatePwdEye() {
    var isText = byId('connPassword').type === 'text';
    byId('connPwdEye').innerHTML = isText ? UI.eyeOff : UI.eye;
    byId('connTogglePwd').setAttribute('aria-label', isText ? '隐藏密码' : '显示密码');
  }

  /**
   * 同步数据库类型相关的 UI。
   * @param {{keepPort?: boolean}} [opts] keepPort=true 时保留端口当前值（编辑回填用）
   */
  function applyTypeUI(opts) {
    opts = opts || {};
    var meta = typeMeta(state.typeId);

    byId('connDbValue').innerHTML = (DB_ICON[meta.id] || '') + '<span>' + escapeHtml(meta.label) + '</span>';

    // 待开发 / 内置库提示 + 按钮可用性
    var alertEl = byId('connUnsupportedAlert');
    if (meta.supported) {
      alertEl.classList.add('hidden');
    } else {
      byId('connUnsupportedText').textContent = meta.builtin ? SQLITE_TEXT : UNSUPPORTED_TEXT;
      alertEl.classList.remove('hidden');
    }
    byId('testConn').disabled = !meta.supported;
    byId('saveConn').disabled = !meta.supported;

    // 端口默认值联动
    var portInput = byId('connPort');
    if (meta.builtin) {
      portInput.value = '';
      portInput.placeholder = '本地文件数据库不需要';
    } else {
      portInput.placeholder = '';
      if (!opts.keepPort || !portInput.value) portInput.value = String(meta.port);
    }

    // SQLite：主机/端口/用户名/密码 置灰并标注
    LOCAL_LOCKED_FIELDS.forEach(function (id) {
      var input = byId(id);
      input.disabled = Boolean(meta.builtin);
      var field = input.closest('.conn-field');
      if (field) {
        var note = field.querySelector('[data-local-note]');
        if (note) note.classList.toggle('hidden', !meta.builtin);
      }
    });

    renderGuide(meta);
    renderDbMenu();
  }

  function setType(id) {
    state.typeId = id;
    state.formTested = false;
    applyTypeUI();
    clearErrors();
  }

  /* ====================== 表单：校验 / 采集 / 回填 / 重置 ====================== */
  function clearErrors() {
    var fields = document.querySelectorAll('#connectionForm .conn-field.has-error');
    Array.prototype.forEach.call(fields, function (f) {
      f.classList.remove('has-error');
      var err = f.querySelector('.conn-field-error');
      if (err) err.textContent = '';
    });
  }

  function setFieldError(fieldName, message) {
    var field = document.querySelector('#connectionForm .conn-field[data-field="' + fieldName + '"]');
    if (!field) return;
    field.classList.add('has-error');
    var err = field.querySelector('.conn-field-error');
    if (err) err.textContent = message;
  }

  function collectPayload() {
    var payload = {
      id: state.editingId || '',
      name: byId('connName').value.trim(),
      dbType: state.typeId,
      host: byId('connHost').value.trim(),
      port: byId('connPort').value ? parseInt(byId('connPort').value, 10) : '',
      user: byId('connUser').value.trim(),
      password: byId('connPassword').value,
      database: byId('connDatabase').value.trim(),
      charset: byId('connCharset').value
    };
    // 编辑已有连接时透传 ssl 字段，避免服务端将其重置（本页不做 SSL 编辑）
    if (state.editingItem) {
      payload.sslEnabled = Boolean(state.editingItem.sslEnabled);
      payload.sslCa = state.editingItem.sslCa || '';
      payload.sslCert = state.editingItem.sslCert || '';
      payload.sslKey = state.editingItem.sslKey || '';
    }
    return payload;
  }

  /** 校验；返回 true 表示通过（仅 MySQL 可保存，SQLite/待开发类型在按钮层已禁用） */
  function validateForm() {
    clearErrors();
    var data = collectPayload();
    var firstInvalid = null;

    function fail(fieldName, inputId, message) {
      setFieldError(fieldName, message);
      if (!firstInvalid) firstInvalid = inputId;
    }

    if (!data.name) fail('connName', 'connName', '请输入连接名称');
    if (!data.host) fail('connHost', 'connHost', '请输入主机地址');
    if (!data.port) fail('connPort', 'connPort', '请输入端口');
    else if (data.port < 1 || data.port > 65535) fail('connPort', 'connPort', '端口需为 1 - 65535 之间的整数');
    if (!data.user) fail('connUser', 'connUser', '请输入用户名');
    if (!data.password && !state.editingId) fail('connPassword', 'connPassword', '请输入密码');

    if (firstInvalid) {
      var el = byId(firstInvalid);
      if (el) el.focus();
      showToast('error', '请检查表单中标红的必填项');
      return false;
    }
    return true;
  }

  function fillForm(item) {
    state.editingId = item.id || '';
    state.editingItem = item || null;
    state.formTested = false;
    state.typeId = item.dbType || 'mysql';

    byId('connName').value = item.name || '';
    byId('connHost').value = item.host || '';
    byId('connUser').value = item.user || '';
    byId('connPassword').value = '';
    byId('connPassword').placeholder = '编辑已有连接时留空表示不修改';
    byId('connDatabase').value = item.database || '';
    byId('connCharset').value = item.charset || 'utf8mb4';

    clearErrors();
    applyTypeUI({ keepPort: true });
    if (item.port) byId('connPort').value = String(item.port);
    byId('connPassword').type = 'password';
    updatePwdEye();
    setEditMode(true);
  }

  function resetForm() {
    state.editingId = '';
    state.editingItem = null;
    state.formTested = false;
    state.typeId = 'mysql';

    byId('connName').value = '';
    byId('connHost').value = '';
    byId('connUser').value = '';
    byId('connPassword').value = '';
    byId('connPassword').placeholder = '请输入密码';
    byId('connDatabase').value = '';
    byId('connDbList').innerHTML = '';
    byId('connCharset').value = 'utf8mb4';
    byId('connPassword').type = 'password';

    clearErrors();
    applyTypeUI();
    updatePwdEye();
    setEditMode(false);
  }

  function setEditMode(editing) {
    // 页面顶部标题块已移除（2026-09-12，为省 43px 纵向空间、减少滚动）：
    // 「新建/编辑」文案改为同步到浏览器标签标题；#pageTitle 仍是视觉隐藏的 h1，
    // 存在则一并更新（保留空值保护，避免节点被再次移除时抛 null 引用）。
    var label = editing ? '编辑数据库连接' : '新建数据库连接';
    document.title = label + ' - 数据导表工具';
    var titleEl = byId('pageTitle');
    if (titleEl) { titleEl.textContent = label; }
  }

  /* ====================== 测试连接 / 保存 / 删除 / 复制 ====================== */
  async function testConnection() {
    var meta = typeMeta(state.typeId);
    if (!meta.supported) {
      showToast('warning', meta.builtin ? SQLITE_TEXT : '该数据库类型功能待开发中，暂不支持测试连接');
      return;
    }
    var payload = collectPayload();
    if (!payload.host || !payload.port || !payload.user) {
      showToast('warning', '请先填写主机、端口和用户名');
      return;
    }
    payload.database = payload.database || '';

    var btn = byId('testConn');
    var original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '测试中...';
    try {
      const result = await requestJson('/api/connections/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      // 测试成功：把可见数据库填进 datalist（输入框可手填也可候选选择）
      byId('connDbList').innerHTML = (result.databases || [])
        .map(function (name) { return '<option value="' + escapeHtml(name) + '"></option>'; })
        .join('');
      state.formTested = true;
      // 表单测试成功：当前编辑行直接落「已连接」，不用等下一轮定时探测
      if (state.editingId) {
        state.probe[state.editingId] = {
          status: 'ok',
          reason: '',
          at: Date.now(),
          version: result.version || '',
          databasesCount: (result.databases || []).length
        };
        state.lastAt = Date.now();
        updateRowStatus(state.editingId);
        renderProbeHint();
      }
      showToast('success', '连接成功：MySQL ' + (result.version || '') +
        ((result.databases || []).length ? '，可见 ' + result.databases.length + ' 个数据库' : ''));
    } finally {
      btn.disabled = !typeMeta(state.typeId).supported;
      btn.textContent = original;
    }
  }

  async function saveConnection(event) {
    if (event) event.preventDefault();
    if (state.saving) return;
    var meta = typeMeta(state.typeId);
    if (!meta.supported) {
      showToast('warning', meta.builtin ? SQLITE_TEXT : '该数据库类型功能待开发中，无法保存');
      return;
    }
    if (!validateForm()) return;

    state.saving = true;
    var saveBtn = byId('saveConn');
    saveBtn.disabled = true;
    try {
      const payload = await requestJson('/api/connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectPayload()),
      });
      var saved = payload.connection || {};
      // 保存前刚测试通过 → 先落「已连接」，紧随其后的列表重载会再探测一次校准
      if (state.formTested && saved.id) {
        state.probe[saved.id] = {
          status: 'ok', reason: '', at: Date.now(), version: '', databasesCount: 0
        };
      }
      var isNew = !state.editingId;
      await loadConnections();
      resetForm();
      showToast('success', (isNew ? '连接「' : '连接「') + (saved.name || '') + '」已' + (isNew ? '保存' : '更新'));
    } finally {
      state.saving = false;
      saveBtn.disabled = !typeMeta(state.typeId).supported;
    }
  }

  async function copyConnection(item) {
    // 复制为一条新连接：密码为敏感信息不可回读，副本需编辑后补填密码
    var copyPayload = {
      id: '',
      name: (item.name || '未命名连接') + '-副本',
      dbType: item.dbType || 'mysql',
      host: item.host || '',
      port: item.port || '',
      user: item.user || '',
      password: '',
      database: item.database || '',
      charset: item.charset || 'utf8mb4',
      sslEnabled: Boolean(item.sslEnabled),
      sslCa: item.sslCa || '',
      sslCert: item.sslCert || '',
      sslKey: item.sslKey || ''
    };
    await requestJson('/api/connections', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(copyPayload),
    });
    await loadConnections();
    showToast('success', '已复制为「' + copyPayload.name + '」（密码需编辑后重新填写）');
  }

  async function deleteConnection(item) {
    var ok = await confirmDialog('删除连接', '确定要删除连接「' + item.name + '」吗？删除后不可恢复。', '删除');
    if (!ok) return;
    await requestJson('/api/connections?id=' + encodeURIComponent(item.id), { method: 'DELETE' });
    if (state.editingId === item.id) resetForm();
    delete state.probe[item.id];
    await loadConnections();
    showToast('success', '连接「' + item.name + '」已删除');
  }

  /* ====================== 事件绑定 ====================== */
  function bindEvents() {
    // 数据库类型自定义下拉
    byId('connDbTrigger').addEventListener('click', function (e) {
      e.stopPropagation();
      openDbMenu(byId('connDbMenu').classList.contains('hidden'));
    });
    byId('connDbMenu').addEventListener('click', function (e) {
      var option = e.target.closest('.conn-db-option');
      if (!option) return;
      setType(option.getAttribute('data-type'));
      openDbMenu(false);
    });
    document.addEventListener('click', function () { openDbMenu(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') openDbMenu(false);
    });

    // 密码明文切换
    byId('connTogglePwd').addEventListener('click', function () {
      var pwd = byId('connPassword');
      pwd.type = pwd.type === 'password' ? 'text' : 'password';
      updatePwdEye();
    });

    // 端口上下箭头
    Array.prototype.forEach.call(document.querySelectorAll('.conn-spinner button'), function (btn) {
      btn.addEventListener('click', function () {
        var target = byId(btn.getAttribute('data-target'));
        if (!target || target.disabled) return;
        var step = parseInt(btn.getAttribute('data-step'), 10);
        var current = target.value ? parseInt(target.value, 10) : 0;
        var next = current + step;
        if (next < 1) next = 1;
        if (next > 65535) next = 65535;
        target.value = String(next);
      });
    });

    // 输入即清除错误
    Array.prototype.forEach.call(document.querySelectorAll('#connectionForm .conn-input'), function (el) {
      el.addEventListener('input', function () {
        var field = el.closest('.conn-field');
        if (field && field.classList.contains('has-error')) {
          field.classList.remove('has-error');
          var err = field.querySelector('.conn-field-error');
          if (err) err.textContent = '';
        }
      });
    });

    // 测试 / 保存
    byId('testConn').addEventListener('click', function () {
      testConnection().catch(function (error) { showToast('error', error.message); });
    });
    byId('connectionForm').addEventListener('submit', function (event) {
      saveConnection(event).catch(function (error) { showToast('error', error.message); });
    });

    // 列表工具条
    byId('connListSearch').addEventListener('input', function (e) {
      state.keyword = e.target.value;
      renderTable();
    });
    byId('connListTypeFilter').addEventListener('change', function (e) {
      state.typeFilter = e.target.value;
      renderTable();
    });
    // 「刷新状态」：重新拉取列表 + 重新探测一轮（原纯图标「刷新列表」按钮已合并到此处）
    byId('connStatusRefreshBtn').addEventListener('click', function () {
      var btn = byId('connStatusRefreshBtn');
      btn.classList.add('spinning');
      window.setTimeout(function () { btn.classList.remove('spinning'); }, 700);
      loadConnections()
        .then(function () { showToast('info', '已刷新列表，正在检测连接状态'); })
        .catch(function (error) { showToast('error', error.message); });
    });
    byId('connNewBtn').addEventListener('click', function () {
      resetForm();
      showToast('info', '已切换为新建连接');
    });

    // 行操作（事件委托）
    byId('connTbody').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-action]');
      if (!btn) return;
      var tr = e.target.closest('tr');
      if (!tr) return;
      var item = findConnection(tr.getAttribute('data-id'));
      if (!item) return;

      var action = btn.getAttribute('data-action');
      if (action === 'edit') {
        fillForm(item);
        showToast('info', '正在编辑「' + item.name + '」');
      } else if (action === 'copy') {
        copyConnection(item).catch(function (error) { showToast('error', error.message); });
      } else if (action === 'delete') {
        deleteConnection(item).catch(function (error) { showToast('error', error.message); });
      }
    });

    // 页面从后台切回前台：立刻补探一次（后台标签页不探测，避免无谓开销）
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) return;
      probeAll(false);
    });
  }

  /* ====================== 初始化 ====================== */
  function init() {
    renderDbTypeList();
    renderDbMenu();
    renderTypeFilter();
    applyTypeUI();
    updatePwdEye();
    bindEvents();
    loadConnections().catch(function (error) { showToast('error', error.message); });
    scheduleProbe();
    setEditMode(false);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
