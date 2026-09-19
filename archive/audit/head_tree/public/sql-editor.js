/*!
 * sql-editor.js — 给页面上的 SQL 输入框加「语法高亮 + 一键排版」
 *
 * 设计要点：
 * - 零依赖、离线可用（本工具常在内网/离线机器上跑，不引 CDN）。
 * - 非侵入：不改动业务代码，靠覆写 textarea.value 的 getter/setter 保持高亮图层同步。
 * - 高亮实现：textarea 文字透明 + 下层 <pre> 叠放同样内容并着色（业界通用做法）。
 * - 排版安全：排版后重新分词比对，token 序列不一致就放弃（宁可不排，也不改坏 SQL）。
 */
(function (global, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (global) global.SqlEditor = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  /* ------------------------------------------------------------------ *
   * 词法
   * ------------------------------------------------------------------ */

  // 结构性关键字：高亮 + 排版时统一大写
  var KEYWORDS = words(
    "ADD ALL ALTER AND AS ASC BETWEEN BY CASE CHANGE COLUMN COMMIT CONSTRAINT CREATE CROSS " +
    "DATABASE DEFAULT DELETE DESC DESCRIBE DISTINCT DROP ELSE END EXISTS EXPLAIN FOREIGN FROM " +
    "FULL FUNCTION GROUP HAVING IF IGNORE IN INNER INSERT INTERSECT INTO IS JOIN KEY LEFT LIKE LIMIT " +
    "NOT NULL OFFSET ON OR ORDER OUTER OVER PARTITION PRIMARY PROCEDURE REFERENCES RENAME REPLACE " +
    "RETURNING RIGHT ROLLBACK ROWS SELECT SET SHOW TABLE THEN TO TRANSACTION TRIGGER TRUNCATE UNION " +
    "UNIQUE UPDATE USE USING VALUES VIEW WHEN WHERE WHILE WINDOW WITH"
  );

  // 常见函数：仅当后面紧跟 "(" 时按函数着色并大写
  var FUNCTIONS = words(
    "ABS ADDDATE ASCII AVG BIN CAST CEIL CEILING CHAR CHAR_LENGTH CHARACTER_LENGTH COALESCE CONCAT " +
    "CONCAT_WS CONVERT COUNT CUME_DIST CURDATE CURTIME DATE DATE_ADD DATE_FORMAT DATE_SUB DATEDIFF " +
    "DAY DAYOFMONTH DAYOFWEEK DAYOFYEAR DENSE_RANK ELT FIELD FIRST_VALUE FLOOR FORMAT FOUND_ROWS " +
    "FROM_UNIXTIME GREATEST GROUP_CONCAT HEX HOUR IF IFNULL INSTR ISNULL JSON_ARRAY JSON_ARRAYAGG " +
    "JSON_EXTRACT JSON_LENGTH JSON_OBJECT JSON_UNQUOTE LAG LAST_DAY LAST_INSERT_ID LAST_VALUE LEAD " +
    "LEAST LENGTH LOCATE LOWER LPAD LTRIM MAX MD5 MID MIN MINUTE MOD MONTH NOW NTH_VALUE NTILE " +
    "NULLIF PERCENT_RANK POWER QUARTER RANK REGEXP_REPLACE REPEAT REPLACE REVERSE ROUND ROW_COUNT " +
    "ROW_NUMBER RPAD RTRIM SECOND SPACE SQRT STDDEV STR_TO_DATE SUBDATE SUBSTR SUBSTRING SYSDATE " +
    "TIME TIMESTAMP TIMESTAMPDIFF TRIM TRUNCATE UNHEX UNIX_TIMESTAMP UPPER UUID VARIANCE WEEK YEAR " +
    "SUM STD"
  );

  // 排版时另起一行的「整句」短语（两词一体，如 GROUP BY / INSERT INTO）
  var PHRASE_BREAK = words2([
    "GROUP BY", "ORDER BY", "INSERT INTO", "DELETE FROM", "UNION ALL", "UNION DISTINCT",
    "CREATE TABLE", "DROP TABLE", "ALTER TABLE", "TRUNCATE TABLE", "CREATE VIEW", "CREATE INDEX"
  ]);

  // 排版时另起一行的单个关键字
  var BREAK_SINGLE = words(
    "SELECT FROM WHERE HAVING LIMIT OFFSET UNION VALUES SET JOIN ON WITH " +
    "UPDATE DELETE INSERT CREATE ALTER DROP TRUNCATE RETURNING USING WINDOW STRAIGHT_JOIN"
  );

  function words(text) {
    var out = Object.create(null);
    text.split(/\s+/).forEach(function (w) { if (w) out[w] = true; });
    return out;
  }
  function words2(list) {
    var out = Object.create(null);
    list.forEach(function (w) { out[w] = true; });
    return out;
  }

  var WORD_START = /[A-Za-z_@$\u4e00-\u9fff]/;
  var WORD_CHAR = /[A-Za-z0-9_@$\u4e00-\u9fff]/;
  var OP_CHARS = "=<>!+-*/%|&^~:";
  var PUNC_CHARS = "(),;.";

  /**
   * 分词。保留空白 token（type: "ws"），便于高亮时原样输出。
   * 每个 token 附带：
   *   nl          该 token 前有换行
   *   blankBefore 该 token 前有空行（>=2 个换行）
   *   spaceBefore 该 token 前有空白（用于判断 `COUNT(` 还是 `AS (`）
   */
  function tokenize(sql) {
    var text = sql == null ? "" : String(sql);
    var toks = [];
    var i = 0;
    var n = text.length;
    var nl = false;
    var blank = false;
    var space = false;

    function put(type, value) {
      if (!value) return;
      toks.push({ type: type, value: value, nl: nl, blankBefore: blank, spaceBefore: space });
      nl = false;
      blank = false;
      space = false;
    }

    while (i < n) {
      var c = text[i];

      // 空白：作为 token 保留（高亮时要原样输出），同时记下换行/空行/空格标记
      if (/\s/.test(c)) {
        var jw = i;
        var breaks = 0;
        while (jw < n && /\s/.test(text[jw])) {
          if (text[jw] === "\n") breaks += 1;
          jw += 1;
        }
        if (nl || breaks > 1) blank = true;
        if (breaks > 0) nl = true;
        space = true;
        toks.push({ type: "ws", value: text.slice(i, jw), nl: false, blankBefore: false, spaceBefore: false });
        i = jw;
        continue;
      }

      // -- 行注释
      if (c === "-" && text[i + 1] === "-") {
        var j1 = i;
        while (j1 < n && text[j1] !== "\n") j1 += 1;
        put("comment", text.slice(i, j1));
        i = j1;
        continue;
      }
      // # 行注释（MySQL）
      if (c === "#") {
        var j2 = i;
        while (j2 < n && text[j2] !== "\n") j2 += 1;
        put("comment", text.slice(i, j2));
        i = j2;
        continue;
      }
      // /* 块注释 */
      if (c === "/" && text[i + 1] === "*") {
        var end = text.indexOf("*/", i + 2);
        var j3 = end === -1 ? n : end + 2;
        put("comment", text.slice(i, j3));
        i = j3;
        continue;
      }
      // '...' 字符串
      if (c === "'") {
        var j4 = i + 1;
        while (j4 < n) {
          if (text[j4] === "\\") { j4 += 2; continue; }
          if (text[j4] === "'") {
            if (text[j4 + 1] === "'") { j4 += 2; continue; }
            j4 += 1;
            break;
          }
          j4 += 1;
        }
        put("string", text.slice(i, j4));
        i = j4;
        continue;
      }
      // "..." 字符串/标识符
      if (c === '"') {
        var j5 = i + 1;
        while (j5 < n) {
          if (text[j5] === "\\") { j5 += 2; continue; }
          if (text[j5] === '"') { j5 += 1; break; }
          j5 += 1;
        }
        put("string", text.slice(i, j5));
        i = j5;
        continue;
      }
      // `...` 标识符
      if (c === "`") {
        var j6 = i + 1;
        while (j6 < n && text[j6] !== "`") j6 += 1;
        j6 = Math.min(n, j6 + 1);
        put("ident", text.slice(i, j6));
        i = j6;
        continue;
      }
      // 数字
      if (/[0-9]/.test(c) || (c === "." && /[0-9]/.test(text[i + 1] || ""))) {
        var j7 = i;
        while (j7 < n && /[0-9.]/.test(text[j7])) j7 += 1;
        if (/[eE]/.test(text[j7] || "") && /[0-9+-]/.test(text[j7 + 1] || "")) {
          j7 += 2;
          while (j7 < n && /[0-9]/.test(text[j7])) j7 += 1;
        }
        put("number", text.slice(i, j7));
        i = j7;
        continue;
      }
      // 词
      if (WORD_START.test(c)) {
        var j8 = i;
        while (j8 < n && WORD_CHAR.test(text[j8])) j8 += 1;
        put("word", text.slice(i, j8));
        i = j8;
        continue;
      }
      // 运算符
      if (OP_CHARS.indexOf(c) !== -1) {
        var j9 = i;
        while (j9 < n && OP_CHARS.indexOf(text[j9]) !== -1) j9 += 1;
        put("op", text.slice(i, j9));
        i = j9;
        continue;
      }
      // 标点
      if (PUNC_CHARS.indexOf(c) !== -1) {
        put("punc", c);
        i += 1;
        continue;
      }
      put("punc", c);
      i += 1;
    }

    classify(toks);
    return toks;
  }

  /** 把 word token 细分为 kw / func / word（需要向后看一个有效 token） */
  function classify(toks) {
    var sig = [];
    for (var k = 0; k < toks.length; k += 1) {
      if (toks[k].type !== "ws") sig.push(k);
    }
    for (var s = 0; s < sig.length; s += 1) {
      var tok = toks[sig[s]];
      if (tok.type !== "word") continue;
      var up = tok.value.toUpperCase();
      var nxt = s + 1 < sig.length ? toks[sig[s + 1]] : null;
      if (nxt && nxt.type === "punc" && nxt.value === "(" && FUNCTIONS[up]) tok.type = "func";
      else if (KEYWORDS[up]) tok.type = "kw";
    }
  }

  function significant(toks) {
    var out = [];
    for (var i = 0; i < toks.length; i += 1) if (toks[i].type !== "ws") out.push(toks[i]);
    return out;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /* ------------------------------------------------------------------ *
   * 高亮
   * ------------------------------------------------------------------ */

  var TOKEN_CLASS = {
    comment: "sql-t-c",
    string: "sql-t-s",
    number: "sql-t-n",
    ident: "sql-t-i",
    func: "sql-t-f",
    kw: "sql-t-k",
    op: "sql-t-p",
    punc: "sql-t-p",
  };

  function highlight(sql) {
    var toks = tokenize(sql);
    var out = "";
    for (var i = 0; i < toks.length; i += 1) {
      var t = toks[i];
      if (t.type === "ws" || t.type === "word") {
        out += escapeHtml(t.value);
        continue;
      }
      var cls = TOKEN_CLASS[t.type];
      out += cls ? '<span class="' + cls + '">' + escapeHtml(t.value) + "</span>" : escapeHtml(t.value);
    }
    return out;
  }

  /* ------------------------------------------------------------------ *
   * 排版
   * ------------------------------------------------------------------ */

  function tokenKey(t) {
    if (t.type === "ws") return "";
    if (t.type === "kw" || t.type === "func") return "k:" + t.value.toUpperCase();
    return t.type + ":" + t.value;
  }

  /** 排版前后 token 序列必须完全一致，否则判定排版失败 */
  function sameTokens(a, b) {
    var ta = significant(tokenize(a));
    var tb = significant(tokenize(b));
    if (ta.length !== tb.length) return false;
    for (var i = 0; i < ta.length; i += 1) {
      if (tokenKey(ta[i]) !== tokenKey(tb[i])) return false;
    }
    return true;
  }

  var JOIN_PREFIX = words("LEFT RIGHT INNER OUTER FULL CROSS NATURAL");
  var LIST_CLAUSES = words("SELECT GROUP ORDER SET VALUES");
  var DEFAULT_WIDTH = 88;

  /** 近似显示宽度：中日韩字符按 2 列算 */
  function tokenWidth(value) {
    var w = 0;
    for (var i = 0; i < value.length; i += 1) {
      w += /[\u2e80-\u9fff\uff00-\uffef]/.test(value[i]) ? 2 : 1;
    }
    return w;
  }

  /** 估算从 start 起到「同层下一个子句」为止的单行宽度，用于决定列表是否拆行 */
  function measureInline(toks, start, limit) {
    var len = 0;
    var dep = 0;
    for (var i = start; i < toks.length; i += 1) {
      var t = toks[i];
      if (t.type === "punc" && t.value === "(") dep += 1;
      else if (t.type === "punc" && t.value === ")") {
        dep -= 1;
        if (dep < 0) break;
      } else if (t.value === ";") break;
      if (dep === 0 && t.type === "kw" && i !== start) {
        var upNext = t.value.toUpperCase();
        var nx = i + 1 < toks.length ? toks[i + 1].value.toUpperCase() : "";
        if (BREAK_SINGLE[upNext] || PHRASE_BREAK[upNext + " " + nx]) break;
      }
      len += tokenWidth(t.value) + 1;
      if (len > limit) return len;
    }
    return len;
  }

  /**
   * SQL 排版。返回排版后的文本；无法安全排版时返回 null。
   * options.indent     结构缩进，默认 4 空格
   * options.subIndent  AND / OR 悬挂缩进，默认 2 空格
   * options.width      单行宽度上限（超过就拆行/拆列），默认 88
   */
  function format(sql, options) {
    var opts = options || {};
    var unit = typeof opts.indent === "string" ? opts.indent : "    ";
    var subUnit = typeof opts.subIndent === "string" ? opts.subIndent : "  ";
    var width = opts.width > 0 ? opts.width : DEFAULT_WIDTH;
    var toks = significant(tokenize(sql));
    if (!toks.length) return null;

    var lines = [];
    var cur = "";
    var curIndent = "";
    var curUnits = 0;
    var prev = null;
    var depthUnits = 0;      // 当前结构缩进（单位数）
    var parenStack = [];     // {block, units}
    var caseStack = [];      // 每个 CASE 起始行的单位数
    var clauseUnits = 0;     // 当前子句自身所在单位数
    var multiline = false;   // 当前子句是否需要拆行

    function flush() {
      var text = cur.replace(/[ \t]+$/, "");
      if (text) lines.push(curIndent + text);
      cur = "";
    }
    function breakTo(units, tok) {
      flush();
      if (tok && tok.blankBefore && lines.length && lines[lines.length - 1] !== "") lines.push("");
      curUnits = Math.max(0, units);
      curIndent = unit.repeat(curUnits);
      prev = null;
    }
    function listLevel() {
      if (!parenStack.length) return true;
      return parenStack[parenStack.length - 1].block === true;
    }
    function emitValue(value, type, spaceBefore) {
      var needSpace = true;
      if (!cur) needSpace = false;
      else if (type === "punc" && (value === "," || value === ")" || value === "." || value === ";")) needSpace = false;
      else if (prev && prev.type === "punc" && (prev.value === "(" || prev.value === ".")) needSpace = false;
      else if (value === "(" && spaceBefore === false) needSpace = false;
      cur += (needSpace ? " " : "") + value;
      prev = { type: type, value: value };
    }
    function emit(tok) {
      var value = tok.value;
      if (tok.type === "kw" || tok.type === "func") value = value.toUpperCase();
      emitValue(value, tok.type, tok.spaceBefore);
    }
    function reset() {
      depthUnits = 0;
      parenStack = [];
      caseStack = [];
      clauseUnits = 0;
      multiline = false;
      curUnits = 0;
      curIndent = "";
      prev = null;
    }

    for (var i = 0; i < toks.length; i += 1) {
      var t = toks[i];
      var up = t.value.toUpperCase();
      var next = i + 1 < toks.length ? toks[i + 1] : null;
      var nextUp = next ? next.value.toUpperCase() : "";
      var prevUp = "";
      for (var b = i - 1; b >= 0; b -= 1) {
        if (toks[b].type !== "comment") { prevUp = toks[b].value.toUpperCase(); break; }
      }

      // 注释
      if (t.type === "comment") {
        if (t.value.slice(0, 2) === "/*" && !t.nl) {
          emit(t); // 行尾块注释，留在原行
        } else {
          breakTo(depthUnits, t);
          emit(t);
          breakTo(depthUnits);
        }
        continue;
      }

      if (t.type === "punc") {
        if (t.value === "(") {
          var isBlock = nextUp === "SELECT" || nextUp === "WITH" || nextUp === "PARTITION" || nextUp === "ORDER";
          emit(t);
          // units：块内容相对「当前行」缩进；depth：开括号前的结构层级，闭合后回退到它
          parenStack.push({ block: isBlock, units: curUnits, depth: depthUnits });
          if (isBlock) {
            depthUnits = curUnits + 1;
            breakTo(depthUnits);
          }
          continue;
        }
        if (t.value === ")") {
          var opened = parenStack.length ? parenStack.pop() : { block: false, depth: depthUnits };
          if (opened.block) {
            depthUnits = Math.max(0, opened.depth);
            breakTo(opened.units); // 闭合括号与开括号所在行左对齐
          }
          emit(t);
          continue;
        }
        if (t.value === ",") {
          emit(t);
          if (multiline && listLevel()) breakTo(clauseUnits + 1);
          continue;
        }
        if (t.value === ";") {
          emit(t);
          flush();
          if (i !== toks.length - 1) lines.push("");
          reset();
          continue;
        }
        emit(t);
        continue;
      }

      // CASE 表达式
      if (up === "CASE" && t.type === "kw") {
        if (multiline) breakTo(curUnits);
        caseStack.push(curUnits);
        emit(t);
        continue;
      }
      if (up === "END" && t.type === "kw") {
        var caseBase = caseStack.length ? caseStack.pop() : curUnits;
        if (multiline) breakTo(caseBase);
        emit(t);
        continue;
      }
      if ((up === "WHEN" || up === "ELSE") && t.type === "kw") {
        var caseTop = caseStack.length ? caseStack[caseStack.length - 1] : curUnits;
        if (multiline) breakTo(caseTop + 1);
        emit(t);
        continue;
      }

      // AND / OR：只在子句层换行（函数括号内保持同行）
      if ((up === "AND" || up === "OR") && t.type === "kw") {
        if (listLevel()) {
          breakTo(Math.max(depthUnits, clauseUnits));
          curIndent = unit.repeat(curUnits) + subUnit;
        }
        emit(t);
        continue;
      }

      var phrase = up + " " + nextUp;
      var isPhrase = t.type === "kw" && PHRASE_BREAK[phrase];
      // LEFT / INNER / OUTER ... 在第一个修饰词处换行，保证 "LEFT JOIN" / "NATURAL LEFT JOIN" 不被拆开
      if (
        t.type === "kw" && JOIN_PREFIX[up] && !JOIN_PREFIX[prevUp] &&
        (nextUp === "JOIN" || nextUp === "OUTER" || JOIN_PREFIX[nextUp])
      ) {
        breakTo(depthUnits, t);
        clauseUnits = depthUnits;
        multiline = false;
        emit(t);
        continue;
      }
      // ON DUPLICATE KEY UPDATE 不属于 JOIN ... ON
      if (up === "ON" && t.type === "kw" && nextUp === "DUPLICATE") {
        emit(t);
        continue;
      }
      var isJoinSuffix = up === "JOIN" && t.type === "kw" && JOIN_PREFIX[prevUp];
      if ((isPhrase || (t.type === "kw" && BREAK_SINGLE[up])) && !isJoinSuffix) {
        var extra = up === "ON" || up === "USING" ? 1 : 0;
        var level = depthUnits + extra;
        breakTo(level, t);
        if (isPhrase) {
          emitValue(phrase, "kw", false);
          i += 1;
        } else {
          emit(t);
        }
        clauseUnits = level;
        multiline = measureInline(toks, i + 1, width) > width;
        if (multiline && LIST_CLAUSES[up]) breakTo(level + 1);
        continue;
      }

      emit(t);
    }

    flush();
    var out = lines.join("\n").replace(/\n{3,}/g, "\n\n").replace(/[ \t]+$/gm, "").trim();
    if (!out) return null;
    return sameTokens(sql, out) ? out : null;
  }

  /* ------------------------------------------------------------------ *
   * 编辑器（DOM）
   * ------------------------------------------------------------------ */

  var ATTR = "data-sql-editor";
  var AUTO_IDS = [
    "querySql", "exportSql", "customSql",
    "beforeAllSql", "afterEachSql", "afterAllSql", "afterQuerySql",
    "beforeSql", "afterSql",
  ];

  var METRIC_PROPS = [
    "font", "letterSpacing", "wordSpacing", "textTransform", "textIndent",
    "tabSize", "padding", "paddingRight", "borderWidth", "borderStyle", "textAlign", "direction",
    "color", "backgroundColor",
  ];

  function attach(textarea) {
    if (!textarea || textarea.tagName !== "TEXTAREA") return null;
    if (textarea.__sqlEditor) return textarea.__sqlEditor;
    if (typeof document === "undefined") return null;

    // ⚠️ getComputedStyle 返回的是「实时」对象：必须在把 textarea 移进 .sql-editor-body 之前
    // 把度量值快照下来，否则读到的是我们自己的透明色/布局值。
    var cs = window.getComputedStyle(textarea);
    var metrics = {};
    for (var mm = 0; mm < METRIC_PROPS.length; mm += 1) {
      metrics[METRIC_PROPS[mm]] = cs[METRIC_PROPS[mm]];
    }

    var shell = document.createElement("div");
    shell.className = "sql-editor";
    var body = document.createElement("div");
    body.className = "sql-editor-body";
    var pre = document.createElement("pre");
    pre.className = "sql-editor-hl";
    pre.setAttribute("aria-hidden", "true");
    var code = document.createElement("code");
    pre.appendChild(code);
    var bar = document.createElement("div");
    bar.className = "sql-editor-bar";
    var tip = document.createElement("span");
    tip.className = "sql-editor-tip";
    var formatBtn = document.createElement("button");
    formatBtn.type = "button";
    formatBtn.className = "sql-editor-btn";
    formatBtn.textContent = "排版";
    formatBtn.title = "格式化 SQL（Ctrl + Shift + F）";
    bar.appendChild(tip);
    bar.appendChild(formatBtn);

    textarea.parentNode.insertBefore(shell, textarea);
    body.appendChild(pre);
    body.appendChild(textarea);
    shell.appendChild(body);
    shell.appendChild(bar);

    for (var m = 0; m < METRIC_PROPS.length; m += 1) {
      var prop = METRIC_PROPS[m];
      if (metrics[prop]) pre.style[prop] = metrics[prop];
    }
    if (!metrics.color || metrics.color === "rgba(0, 0, 0, 0)" || metrics.color === "transparent") {
      pre.style.color = "#26384f"; // 兜底：保证普通标识符可见
    }
    textarea.spellcheck = false;
    textarea.setAttribute("autocapitalize", "off");
    textarea.setAttribute("autocomplete", "off");

    var raf = 0;
    function render() {
      code.innerHTML = highlight(textarea.value) + "\n";
      syncBox();
      syncScroll();
    }
    function refresh() {
      if (raf) return;
      raf = window.requestAnimationFrame(function () {
        raf = 0;
        render();
      });
    }
    function syncBox() {
      // 用 textarea 的 client 尺寸对齐，自动排除滚动条宽度，保证折行位置一致
      var w = textarea.clientWidth;
      var h = textarea.clientHeight;
      if (w > 0) pre.style.width = w + "px";
      if (h > 0) pre.style.height = h + "px";
    }
    function syncScroll() {
      pre.scrollTop = textarea.scrollTop;
      pre.scrollLeft = textarea.scrollLeft;
    }
    // 给右上角工具条预留宽度，保证按钮不会压住 SQL 文字
    var reservedPadRight = -1;
    function reserveBarSpace() {
      var base = parseFloat(metrics.paddingRight) || 0;
      var reserve = bar.offsetWidth ? bar.offsetWidth + 14 : 0;
      var target = base + reserve;
      if (Math.abs(target - reservedPadRight) < 1) return;
      reservedPadRight = target;
      pre.style.paddingRight = target + "px";
      textarea.style.paddingRight = target + "px";
    }

    // 覆写 value：业务代码（打开任务、加载已保存查询等）赋值时自动重绘
    var desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    if (desc && desc.get && desc.set) {
      Object.defineProperty(textarea, "value", {
        configurable: true,
        get: function () { return desc.get.call(textarea); },
        set: function (value) {
          desc.set.call(textarea, value);
          refresh();
        },
      });
    }

    var tipTimer = 0;
    function flash(message) {
      tip.textContent = message;
      tip.classList.add("show");
      window.clearTimeout(tipTimer);
      tipTimer = window.setTimeout(function () {
        tip.textContent = "";
        tip.classList.remove("show");
      }, 1800);
    }

    function applyText(text) {
      var ok = false;
      try {
        var start = textarea.selectionStart;
        var end = textarea.selectionEnd;
        textarea.focus();
        textarea.setSelectionRange(0, textarea.value.length);
        ok = document.execCommand("insertText", false, text); // 保留撤销栈
        if (!ok) textarea.setSelectionRange(start, end);
      } catch (error) {
        ok = false;
      }
      if (!ok) {
        textarea.value = text;
      }
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      refresh();
    }

    function doFormat() {
      var source = textarea.value;
      if (!source.trim()) {
        flash("没有可排版的内容");
        return;
      }
      var result = format(source, { indent: "    ", subIndent: "  " });
      if (!result) {
        flash("排版失败，已保留原文");
        return;
      }
      if (result === source.trim()) {
        flash("已是规范格式");
        return;
      }
      applyText(result);
      flash("已排版");
    }

    textarea.addEventListener("input", refresh);
    textarea.addEventListener("change", refresh);
    textarea.addEventListener("compositionend", refresh);
    textarea.addEventListener("scroll", syncScroll, { passive: true });
    textarea.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && (event.key === "F" || event.key === "f")) {
        event.preventDefault();
        doFormat();
      }
    });
    formatBtn.addEventListener("click", function (event) {
      event.preventDefault();
      doFormat();
    });

    var handle = { textarea: textarea, shell: shell, refresh: refresh, format: doFormat };
    textarea.__sqlEditor = handle;
    textarea.__sqlEditorAttached = true;

    if (window.ResizeObserver) {
      new window.ResizeObserver(function () {
        reserveBarSpace();
        syncBox();
        syncScroll();
      }).observe(textarea);
    }
    window.addEventListener("resize", function () {
      reserveBarSpace();
      syncBox();
    });
    window.requestAnimationFrame(function () {
      reserveBarSpace();
      render();
    });

    return handle;
  }

  function attachAll(root) {
    if (typeof document === "undefined") return 0;
    var scope = root || document;
    var found = [];
    var marked = scope.querySelectorAll("textarea[" + ATTR + "]");
    for (var i = 0; i < marked.length; i += 1) found.push(marked[i]);
    AUTO_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el && el.tagName === "TEXTAREA" && found.indexOf(el) === -1) found.push(el);
    });
    var count = 0;
    found.forEach(function (el) {
      try {
        if (attach(el)) count += 1;
      } catch (error) {
        /* 单个编辑器失败不影响业务 */
      }
    });
    return count;
  }

  /** 只读代码块高亮：返回带 span 的 HTML（调用方塞进 innerHTML） */
  function highlightBlock(text) {
    return highlight(text == null ? "" : String(text));
  }

  var api = {
    tokenize: tokenize,
    highlight: highlight,
    highlightBlock: highlightBlock,
    format: format,
    attach: attach,
    attachAll: attachAll,
    escapeHtml: escapeHtml,
  };

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { attachAll(); });
    } else {
      attachAll();
    }
  }

  return api;
});
