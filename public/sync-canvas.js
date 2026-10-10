/* ==========================================================================
   同步模块 · 无限画布
   --------------------------------------------------------------------------
   三条铁律（改动前先看懂，否则很容易做出自相矛盾的界面）：
     1. **一个配置项只属于一个节点**。典型：「写入方式」由同步模式推导，
        目标节点只有只读块，不给下拉 —— 这样根本不可能出现"模式说 A、目标说 B"。
     2. **候选值来自当前上游对象**。水位列 / 业务键只列当前源连接 + 源表
        （或自定义 SQL）真实存在的列，绝不硬编码"订单创建时间"之类。
     3. **非法组合即选即禁用**。切走增量就自动清空水位列；非 upsert 就整段置灰键列区。
   ========================================================================== */
(function () {
  "use strict";

  var canvasView = document.getElementById("canvasView");
  var stage = document.getElementById("stage");
  var world = document.getElementById("world");
  var edges = document.getElementById("edges");
  var labels = document.getElementById("edgeLabels");
  var zoomVal = document.getElementById("zoomVal");
  var drawer = document.getElementById("drawer");
  var drawerBody = document.getElementById("drawerBody");

  /* ---------------------------------------------------------------- 单一真值源 */
  function blankTask() {
    return {
      id: "",
      name: "",
      sourceConnectionId: "",
      sourceMode: "table",
      sourceTable: "",
      sourceSql: "",
      targetConnectionId: "",
      targetTable: "",
      columns: [],
      syncMode: "full",
      keyColumns: [],
      watermarkColumn: "",
      batchRows: STATE.defaultBatchRows,
      commitMode: "batch",
      stopOnError: true,
      enabled: true
    };
  }
  var STATE = {
    connections: [],
    modes: [],
    probe: null,
    srcTables: [],
    dstTables: [],
    preview: null,
    maxBatchRows: 50000,
    defaultBatchRows: 5000,
    savedSnapshot: "" // 上次落库时的配置快照，用来算顶部条的「已修改 · 未保存」
  };
  var TASK = blankTask();

  /* ---------------------------------------------------------------- 画布状态 */
  var px = 24, py = 24, scale = 1;
  var HEAD_Y = 28;
  var openId = null;

  /* ---------------------------------------------------------------- 流程结构 */
  /* 节点角色是**固定语义**的 —— 后端 _sync_tasks 就是按这几个字段读配置的，
     所以画布上允许增删，但每类最多一个。连线只表达"先做哪步再做哪步"。
     业主 2026-10-10 要求：默认只放起点，点「＋」按需添加后续节点，连线可拖拽改接。 */
  var KINDS = {
    src: { step: "1", ico: "▤", cls: "ico-green", title: "源", level: 0 },
    map: { step: "2", ico: "⇄", cls: "ico-blue", title: "字段映射", level: 1 },
    mode: { step: "3", ico: "◈", cls: "ico-amber", title: "同步模式", level: 2 },
    dst: { step: "4", ico: "✓", cls: "ico-green", title: "目标", level: 3, terminal: true },
    skip: { step: "4", ico: "∅", cls: "ico-gray", title: "本轮跳过", level: 3, terminal: true }
  };
  var KIND_ORDER = ["src", "map", "mode", "dst", "skip"];
  var X0 = 60, Y0 = 40, GAP_X = 480, GAP_Y = 280;

  var NODES = {}; // kind -> {x, y}；键存在 = 这个节点在画布上
  var EDGES = []; // [{from, to, label}]，from / to 都是 kind
  var dragLink = null; // 正在拖拽改接的连线
  var nodeDrag = null; // 正在拖动的节点
  var justLinked = false; // 刚拖完东西，别让这一次点击被当成"点节点开面板 / 点空白关面板"

  /* 任务必须有这三个角色才跑得起来（其余可选） */
  var REQUIRED_NODES = ["src", "mode", "dst"];

  function el(id) { return document.getElementById(id); }
  function nodeId(kind) { return "n-" + kind; }
  function kindOf(id) { return String(id || "").replace(/^n-/, ""); }
  function ids() {
    return KIND_ORDER.filter(function (k) { return !!NODES[k]; }).map(nodeId);
  }
  function esc(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmt(n) { return Number(n || 0).toLocaleString("en-US"); }

  function nodeBox(id) {
    var n = el(id);
    if (!n) return { x: 0, y: 0, w: 380, h: 130 };
    return {
      x: parseFloat(n.style.left) || 0, y: parseFloat(n.style.top) || 0,
      w: n.offsetWidth || 380, h: n.offsetHeight || 130
    };
  }

  /* ---------------------------------------------------------------- 增删节点 */
  function placeFor(kind) {
    // 按层级往右排；同一层已经有节点就往下错开（「目标」和「本轮跳过」必然同层）
    var level = KINDS[kind].level;
    var same = KIND_ORDER.filter(function (k) {
      return NODES[k] && KINDS[k].level === level;
    });
    return { x: X0 + level * GAP_X, y: Y0 + same.length * GAP_Y };
  }

  /* 上游 = 已存在、层级比它小、且层级最大的那个 */
  function upstreamOf(kind) {
    var level = KINDS[kind].level, best = null;
    KIND_ORDER.forEach(function (k) {
      if (!NODES[k] || k === kind) return;
      if (KINDS[k].level >= level) return;
      if (!best || KINDS[k].level > KINDS[best].level) best = k;
    });
    return best;
  }

  /* 只能顺着流程往后连（层级必须更大），避免出现回边把流程绕成环 */
  function canConnect(from, to) {
    if (!from || !to || from === to) return false;
    if (!NODES[from] || !NODES[to]) return false;
    return KINDS[to].level > KINDS[from].level;
  }

  function addEdge(from, to) {
    if (!canConnect(from, to)) return false;
    if (EDGES.some(function (e) { return e.from === from && e.to === to; })) return false;
    var label = "";
    if (from === "mode") {
      // 「同步模式」是唯一有两条出边的节点：先连出去的那条是"是"，后一条是"否"
      label = EDGES.filter(function (e) { return e.from === "mode"; }).length === 0 ? "是" : "否";
    }
    EDGES.push({ from: from, to: to, label: label });
    return true;
  }

  function buildNodeDom(kind) {
    var spec = KINDS[kind];
    var node = document.createElement("div");
    node.className = "node" + (kind === "src" ? " start" : (spec.terminal ? " terminal" : ""));
    node.id = nodeId(kind);
    node.dataset.type = kind;
    node.style.left = NODES[kind].x + "px";
    node.style.top = NODES[kind].y + "px";
    node.innerHTML =
      '<div class="n-head">' +
      '<span class="n-step' + (spec.terminal ? " gray" : "") + '">' + spec.step + "</span>" +
      '<span class="n-ico ' + spec.cls + '">' + spec.ico + "</span>" +
      '<span class="n-title">' + esc(spec.title) + "</span>" +
      '<span class="n-state"></span>' +
      "</div>" +
      '<div class="n-lines"></div>' +
      '<div class="n-foot">' +
      '<button class="op-add" type="button" title="从这一步往下加一个节点">＋</button>' +
      '<button class="op-del" type="button" title="从画布移除这个节点"' +
      (kind === "src" ? " disabled" : "") + ">−</button>" +
      "</div>";
    world.appendChild(node);
    return node;
  }

  /* 按 NODES 重建整块画布 DOM（增删节点后调，节点数只有个位数，够快） */
  function rebuildNodes() {
    Array.prototype.forEach.call(world.querySelectorAll(".node"), function (n) { n.remove(); });
    // ⚠️ 这里必须传 kind（"src"），不能传 ids() 里的节点 id（"n-src"）——
    // 它们差一个前缀，传错了 KINDS[kind] 就是 undefined。
    KIND_ORDER.forEach(function (k) { if (NODES[k]) buildNodeDom(k); });
    renumber();
  }

  /* 步骤序号按**实际存在的层级**重排：不加「字段映射」时，同步模式就是第 2 步 */
  function renumber() {
    var levelSeq = {}, seq = 1;
    KIND_ORDER.forEach(function (k) {
      if (!NODES[k]) return;
      var L = KINDS[k].level;
      if (!(L in levelSeq)) levelSeq[L] = seq++;
    });
    KIND_ORDER.forEach(function (k) {
      if (!NODES[k]) return;
      var node = el(nodeId(k));
      var badge = node && node.querySelector(".n-step");
      if (badge) badge.textContent = levelSeq[KINDS[k].level];
    });
  }

  function addNode(kind) {
    if (NODES[kind] || !KINDS[kind]) return;
    NODES[kind] = placeFor(kind);
    var up = upstreamOf(kind);
    if (up) addEdge(up, kind);
    buildNodeDom(kind);
    repaintAll();
    fitView();
  }

  function removeNode(kind) {
    if (!NODES[kind] || kind === "src") return; // 起点不给删，否则流程没有入口
    delete NODES[kind];
    EDGES = EDGES.filter(function (e) { return e.from !== kind && e.to !== kind; });
    var node = el(nodeId(kind));
    if (node) node.remove();
    if (openId === nodeId(kind)) closeDrawer();
    renumber();
    repaintAll();
    fitView();
  }

  /* 把已存任务还原成画布上的节点与连线 */
  function resetFlow(task) {
    NODES = {}; EDGES = [];
    NODES.src = placeFor("src");
    if (task) {
      // 打开已存任务 = 它本来就是配全的，把五个角色都摆出来
      NODES.map = placeFor("map");
      NODES.mode = placeFor("mode");
      NODES.dst = placeFor("dst");
      if ((task.syncMode || "full") === "incremental") NODES.skip = placeFor("skip");
      addEdge("src", "map");
      addEdge("map", "mode");
      addEdge("mode", "dst");
      if (NODES.skip) addEdge("mode", "skip");
    }
    rebuildNodes();
  }

  /* ---------------------------------------------------------------- 连线 */
  function port(b, p, fy) {
    var y = (fy === undefined || fy === null) ? null : b.y + b.h * fy;
    if (p === "r") return { x: b.x + b.w, y: y === null ? b.y + HEAD_Y : y };
    if (p === "l") return { x: b.x, y: y === null ? b.y + HEAD_Y : y };
    if (p === "b") return { x: b.x + b.w / 2, y: b.y + b.h };
    return { x: b.x + b.w / 2, y: b.y };
  }
  function route(a, b, fp, tp, fy) {
    var p1 = port(a, fp, fy), p2 = port(b, tp), d, lx, ly;
    if (fp === "r" && tp === "l") {
      var mx = (p1.x + p2.x) / 2;
      if (Math.abs(p1.y - p2.y) < 3) {
        d = "M " + p1.x + " " + p1.y + " H " + p2.x; lx = (p1.x + p2.x) / 2; ly = p1.y - 12;
      } else {
        d = "M " + p1.x + " " + p1.y + " H " + mx + " V " + p2.y + " H " + p2.x;
        lx = mx; ly = (p1.y + p2.y) / 2;
      }
    } else if (fp === "b" && tp === "t") {
      d = "M " + p1.x + " " + p1.y + " V " + p2.y; lx = p1.x + 26; ly = (p1.y + p2.y) / 2;
    } else {
      var m2 = (p1.x + p2.x) / 2;
      d = "M " + p1.x + " " + p1.y + " H " + m2 + " V " + p2.y + " H " + p2.x;
      lx = m2; ly = (p1.y + p2.y) / 2;
    }
    return { d: d, lx: lx, ly: ly };
  }
  /* 连线 = EDGES 的真实投影。每条线的目标端挂一个可拖的小圆点，
     拖它就改接（业主 2026-10-10 要的"连线可以自定义拖拽"）。 */
  function drawEdges() {
    var paths = "", chips = "", hooks = "";
    EDGES.forEach(function (e, i) {
      if (!NODES[e.from] || !NODES[e.to]) return;
      var a = nodeBox(nodeId(e.from)), b = nodeBox(nodeId(e.to));
      // 「同步模式」有两条出边，错开高度连，否则两条线会叠在一起看不出是两条
      var fy = e.from === "mode" ? (e.label === "否" ? 0.78 : 0.22) : null;
      var r = route(a, b, "r", "l", fy);
      paths += '<path class="edge" d="' + r.d + '" marker-end="url(#ah)"/>';
      // ⚠️ 手柄放**线段中点**，不能贴在目标节点左边缘上 —— 贴上去会被节点本身盖住，
      // 鼠标按到的是节点而不是圆点，拖拽根本不会开始。
      var p1h = port(a, "r", fy), p2h = port(b, "l");
      hooks += '<circle class="edge-hook" data-edge="' + i + '" cx="' + ((p1h.x + p2h.x) / 2) +
        '" cy="' + ((p1h.y + p2h.y) / 2) + '" r="7">' +
        "<title>拖我，把这条线改接到别的节点上</title></circle>";
      if (e.label) {
        chips += '<div class="edge-label" style="left:' + r.lx + "px;top:" + r.ly + 'px">' + e.label + "</div>";
      }
    });
    if (dragLink) {
      // 拖拽中的临时线：从起点节点的右端口跟着鼠标走
      var fromBox = nodeBox(nodeId(dragLink.from));
      var p1 = port(fromBox, "r", dragLink.fy);
      var mx2 = (p1.x + dragLink.x) / 2;
      paths += '<path class="edge temp" d="' +
        (Math.abs(p1.y - dragLink.y) < 3
          ? "M " + p1.x + " " + p1.y + " H " + dragLink.x
          : "M " + p1.x + " " + p1.y + " H " + mx2 + " V " + dragLink.y + " H " + dragLink.x) +
        '"/>';
    }
    var defs = edges.querySelector("defs");
    edges.innerHTML = "";
    if (defs) edges.appendChild(defs);
    edges.insertAdjacentHTML("beforeend", paths + hooks);
    labels.innerHTML = chips;
  }

  /* ---------------------------------------------------------------- 拖拽改接 */
  /* 把屏幕坐标换算成 world 坐标（要减掉画布平移、再除以缩放） */
  function localPoint(evt) {
    var r = stage.getBoundingClientRect();
    return { x: (evt.clientX - r.left - px) / scale, y: (evt.clientY - r.top - py) / scale };
  }
  function nodeAt(clientX, clientY) {
    if (clientX < 0 || clientY < 0) return "";
    var hit = document.elementFromPoint(clientX, clientY);
    var node = hit && hit.closest ? hit.closest(".node") : null;
    return node ? kindOf(node.id) : "";
  }
  function paintDropTargets(hoverKind) {
    var from = dragLink ? dragLink.from : "";
    ids().forEach(function (id) {
      var n = el(id);
      if (!n) return;
      var k = kindOf(id);
      n.classList.toggle("drop-ok", !!from && canConnect(from, k));
      n.classList.toggle("drop-hot", !!hoverKind && hoverKind === k);
    });
  }
  function endLink(clientX, clientY) {
    if (!dragLink) return;
    var to = nodeAt(clientX, clientY);
    var edge = EDGES[dragLink.idx];
    if (edge && to && canConnect(dragLink.from, to) && to !== edge.to) {
      edge.to = to;
      // 改接后可能撞出重复边，去重（同一个 from→to 只留一条）
      EDGES = EDGES.filter(function (x, i, arr) {
        return arr.findIndex(function (y) { return y.from === x.from && y.to === x.to; }) === i;
      });
    }
    dragLink = null;
    canvasView.classList.remove("linking");
    paintDropTargets("");
    suppressNextClick();
    repaintAll();
  }

  /* 拖过东西之后的那一次 click 不该被当成"点空白关面板"或"点节点开面板" */
  function suppressNextClick() {
    justLinked = true;
    setTimeout(function () { justLinked = false; }, 0);
  }

  world.addEventListener("pointerdown", function (e) {
    var hook = e.target.closest(".edge-hook");
    if (hook) {
      var idx = parseInt(hook.dataset.edge, 10);
      var edge = EDGES[idx];
      if (!edge) return;
      // 抢在 stage 的平移之前吃掉这次按下，否则拖线会变成拖画布
      e.stopPropagation();
      e.preventDefault();
      var fy = edge.from === "mode" ? (edge.label === "否" ? 0.78 : 0.22) : null;
      var p0 = localPoint(e);
      dragLink = { from: edge.from, fy: fy, idx: idx, x: p0.x, y: p0.y };
      canvasView.classList.add("linking");
      if (world.setPointerCapture) world.setPointerCapture(e.pointerId);
      drawEdges();
      return;
    }

    /* 拖节点本身 = 挪位置（业主 2026-10-10：节点位置不再固定）。
       按下时**不能**立刻判定为拖动 —— 只点一下是要开配置面板的，
       所以先记下起点，位移超过 4px 才算真的在拖。 */
    var node = e.target.closest(".node");
    if (!node || e.target.closest(".n-foot")) return;
    var kind = kindOf(node.id);
    if (!NODES[kind]) return;
    var p = localPoint(e);
    nodeDrag = {
      kind: kind, dx: p.x - NODES[kind].x, dy: p.y - NODES[kind].y,
      sx: e.clientX, sy: e.clientY, moved: false
    };
    e.stopPropagation();
    if (world.setPointerCapture) world.setPointerCapture(e.pointerId);
  });

  world.addEventListener("pointermove", function (e) {
    if (dragLink) {
      var p = localPoint(e);
      dragLink.x = p.x;
      dragLink.y = p.y;
      paintDropTargets(nodeAt(e.clientX, e.clientY));
      drawEdges();
      return;
    }
    if (!nodeDrag) return;
    if (!nodeDrag.moved) {
      if (Math.abs(e.clientX - nodeDrag.sx) + Math.abs(e.clientY - nodeDrag.sy) < 4) return;
      nodeDrag.moved = true;
      var moving = el(nodeId(nodeDrag.kind));
      if (moving) moving.classList.add("moving");
    }
    var pt = localPoint(e);
    var nx = Math.max(0, Math.round(pt.x - nodeDrag.dx));
    var ny = Math.max(0, Math.round(pt.y - nodeDrag.dy));
    NODES[nodeDrag.kind] = { x: nx, y: ny };
    var n = el(nodeId(nodeDrag.kind));
    if (n) { n.style.left = nx + "px"; n.style.top = ny + "px"; }
    drawEdges(); // 连线跟着节点走
  });
  world.addEventListener("pointerup", function (e) {
    if (nodeDrag) {
      var dragged = nodeDrag.moved;
      var n = el(nodeId(nodeDrag.kind));
      if (n) n.classList.remove("moving");
      nodeDrag = null;
      if (dragged) suppressNextClick(); // 拖完别顺手把配置面板弹出来
      return;
    }
    endLink(e.clientX, e.clientY);
  });
  world.addEventListener("pointercancel", function () {
    if (nodeDrag) {
      var n = el(nodeId(nodeDrag.kind));
      if (n) n.classList.remove("moving");
      nodeDrag = null;
      return;
    }
    endLink(-1, -1);
  });

  /* ---------------------------------------------------------------- 新增节点 */
  /* 点节点上的「＋」才列出**还没加过**的角色 —— 默认画布上只有起点，
     业主 2026-10-10 明确要求"不要一上来就铺满 5 个节点"。 */
  function openAddMenu(btn) {
    var menu = el("addMenu");
    var list = KIND_ORDER.filter(function (k) { return !NODES[k]; });
    if (!list.length) {
      menu.hidden = true;
      showDialog("节点已经齐了", "五个角色都已在画布上。可以用节点上的「−」移除，或拖连线改接。");
      return;
    }
    var nextLevel = Math.min.apply(null, list.map(function (k) { return KINDS[k].level; }));
    menu.innerHTML = list.map(function (k) {
      var spec = KINDS[k];
      return '<button type="button" class="am-item" data-kind="' + k + '">' +
        '<span class="am-ico ' + spec.cls + '">' + spec.ico + "</span>" +
        '<span class="am-title">' + esc(spec.title) + "</span>" +
        (KINDS[k].level === nextLevel ? '<span class="am-rec">推荐</span>' : "") +
        "</button>";
    }).join("");
    var r = btn.getBoundingClientRect(), host = el("canvasBody").getBoundingClientRect();
    menu.hidden = false;
    var left = r.left - host.left, top = r.bottom - host.top + 6;
    if (left + menu.offsetWidth > host.width - 8) left = Math.max(8, host.width - menu.offsetWidth - 8);
    if (top + menu.offsetHeight > host.height - 8) top = Math.max(8, r.top - host.top - menu.offsetHeight - 6);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }
  el("addMenu").addEventListener("click", function (e) {
    var item = e.target.closest(".am-item");
    if (!item) return;
    e.stopPropagation();
    el("addMenu").hidden = true;
    addNode(item.dataset.kind);
  });

  /* 节点上的三个动作：＋ 加节点、− 删节点、点其它地方开配置面板。
     必须用**事件委托**（节点是动态增删的，逐个绑定一定会漏）。 */
  world.addEventListener("click", function (e) {
    if (justLinked) return; // 刚拖动过节点/连线，这一次 click 不算数
    /* ⚠️ 绝对不能用 e.target —— 节点头上的 pointerdown 里做了 setPointerCapture，
     之后的 click 目标会被换成**捕获元素**（world 自己），closest(".node") 永远为空。
     表现就是"点节点打不开面板"，而且还会被 stage 当成点了空白把面板关掉。
     必须用坐标重新问一次"光标底下到底是谁"。 */
    var hit = document.elementFromPoint(e.clientX, e.clientY) || e.target;
    var pick = function (sel) { return hit && hit.closest ? hit.closest(sel) : null; };
    var node = pick(".node");
    var addBtn = pick(".op-add");
    var delBtn = pick(".op-del");
    if (addBtn && node) { e.stopPropagation(); openAddMenu(addBtn); return; }
    if (delBtn && node) { e.stopPropagation(); removeNode(kindOf(node.id)); return; }
    if (node) {
      e.stopPropagation(); // 别让 stage 把这次点击当成"点空白"关掉刚打开的面板
      setTimeout(function () { openDrawer(node.id); }, 0);
    }
  });
  function apply() {
    world.style.transform = "translate(" + px + "px," + py + "px) scale(" + scale + ")";
    zoomVal.textContent = Math.round(scale * 100) + "%";
    drawEdges();
  }

  /* ---------------------------------------------------------------- 平移 / 缩放 */
  var panning = false, sx = 0, sy = 0, spx = 0, spy = 0;
  /* ⚠️ 点在对话框里也要排除。`<dialog>` 是 `.stage` 的子元素，不排除的话
     点「×」「确定」会先被当成"按空白处准备拖画布"，紧接着 setPointerCapture 把
     指针事件全劫到 stage —— 结果按钮的 click 根本不触发，对话框关不掉。 */
  function inChrome(target) {
    return !!(target.closest(".node") || target.closest(".flow-hud") ||
      target.closest(".flow-actions") || target.closest(".drawer") ||
      target.closest(".canvas-bar") || target.closest(".add-menu") ||
      target.closest("dialog"));
  }
  stage.addEventListener("pointerdown", function (e) {
    if (inChrome(e.target)) return;
    panning = true; stage.classList.add("dragging");
    sx = e.clientX; sy = e.clientY; spx = px; spy = py;
    if (stage.setPointerCapture) stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener("pointermove", function (e) {
    if (!panning) return;
    px = spx + (e.clientX - sx); py = spy + (e.clientY - sy); apply();
  });
  stage.addEventListener("pointerup", function () { panning = false; stage.classList.remove("dragging"); });
  stage.addEventListener("click", function (e) {
    // ⚠️ 面板里点一下会就地重绘（如切换同步模式），被点元素当场从 DOM 移除 ——
    // 它的祖先链已断，closest(".drawer") 匹配不上，会被误判成"点了空白"而关掉面板。
    if (!e.target.isConnected) return;
    if (justLinked) return; // 刚拖完连线的那一下 click 不算"点空白"
    if (inChrome(e.target)) return;
    el("addMenu").hidden = true;
    closeDrawer();
  });
  stage.addEventListener("wheel", function (e) {
    e.preventDefault();
    var r = stage.getBoundingClientRect();
    var cx = e.clientX - r.left, cy = e.clientY - r.top;
    var next = Math.min(1.8, Math.max(0.35, scale * (e.deltaY < 0 ? 1.1 : 0.9)));
    px = cx - (cx - px) * (next / scale);
    py = cy - (cy - py) * (next / scale);
    scale = next; apply();
  }, { passive: false });

  function zoomBy(f) {
    var r = stage.getBoundingClientRect(), cx = r.width / 2, cy = r.height / 2;
    var next = Math.min(1.8, Math.max(0.35, scale * f));
    px = cx - (cx - px) * (next / scale);
    py = cy - (cy - py) * (next / scale);
    scale = next; apply();
  }
  function fitView() {
    var list = ids();
    if (!list.length) return;
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    list.forEach(function (id) {
      var b = nodeBox(id);
      minX = Math.min(minX, b.x); minY = Math.min(minY, b.y);
      maxX = Math.max(maxX, b.x + b.w); maxY = Math.max(maxY, b.y + b.h);
    });
    var bw = maxX - minX, bh = maxY - minY, r = stage.getBoundingClientRect();
    // ⚠️ 四周必须留边距再算缩放，否则居中的那一项会算成 0、节点贴到左上角被裁掉。
    // 面板打开时 stage 自己已经变窄（margin-right 让出 632px），所以这里不用再预留。
    var M = 56;
    var availW = Math.max(320, r.width - M * 2);
    var availH = Math.max(240, r.height - M * 2);
    scale = Math.min(availW / bw, availH / bh, 1.1);
    px = M + (availW - bw * scale) / 2 - minX * scale;
    py = M + (availH - bh * scale) / 2 - minY * scale;
    apply();
  }
  el("zoomIn").onclick = function () { zoomBy(1.15); };
  el("zoomOut").onclick = function () { zoomBy(1 / 1.15); };
  el("fit").onclick = fitView;

  /* ---------------------------------------------------------------- 派生：模式语义
     「写入方式」刻意没有一个自己的字段 —— 它永远由 syncMode 算出来。 */
  var WRITE_LONG = {
    full: "清空目标表后写入（先 delete 清空，再在同一个事务里按源全量重写，失败整体回滚）",
    upsert: "按业务键更新 / 插入（先删掉键相同的旧行再插新的；目标端多出来的行保留）",
    incremental: "按水位追加 / 更新（只处理水位之后的行，不清空目标表）",
    append: "直接插入，不去重"
  };
  var WRITE_SHORT = {
    full: "清空后写入",
    upsert: "按业务键更新/插入",
    incremental: "按水位追加/更新",
    append: "直接插入"
  };
  var MODE_STATE = {
    full: { text: "全量覆盖", st: "ok" },
    upsert: { text: "upsert · 需键列", st: "warn" },
    incremental: { text: "增量 · 有局限", st: "warn" },
    append: { text: "仅追加 · 会翻倍", st: "warn" }
  };
  function modeLabel(mode) {
    var hit = STATE.modes.filter(function (m) { return m.value === mode; })[0];
    return hit ? hit.label : mode;
  }
  function srcColumnNames() {
    return (STATE.probe && STATE.probe.columns || []).map(function (c) { return c.name; });
  }
  function probeRows() { return STATE.probe ? STATE.probe.rowCount : null; }
  function writeColumns() {
    return TASK.columns.length ? TASK.columns : srcColumnNames();
  }
  function connLabel(connId) {
    var hit = STATE.connections.filter(function (c) { return c.id === connId; })[0];
    if (!hit) return "";
    return hit.name + " · " + (hit.database || "");
  }
  function estimateSecs() {
    var rows = TASK.syncMode === "incremental" ? Math.round((probeRows() || 0) / 4) : (probeRows() || 0);
    return Math.max(1, Math.round(rows / 21470));
  }

  /* ---------------------------------------------------------------- 卡面派生 */
  function cardOf(id) {
    var type = el(id).dataset.type;
    if (type === "src") {
      var how = TASK.sourceMode === "sql" ? "自定义 SQL" : "整表";
      var rows = probeRows();
      return {
        title: "源", ico: "▤", cls: "ico-green",
        state: rows === null ? "待探测" : fmt(rows) + " 行", st: rows === null ? "" : "ok",
        lines: [
          // 任务名放在起点节点上：整条流程是"为了这个任务"在跑，第一眼看的就是它。
          ["任务", (TASK.enabled ? "" : "（已停用）") + (TASK.name || "未命名任务")],
          ["数据源", TASK.sourceConnectionId ? connLabel(TASK.sourceConnectionId) : "未选择"],
          ["取数方式", how + (TASK.sourceMode === "table" && TASK.sourceTable ? " · " + TASK.sourceTable : "")],
          ["列数", srcColumnNames().length ? srcColumnNames().length + " 列" : "未探测"]
        ]
      };
    }
    if (type === "map") {
      var total = srcColumnNames().length;
      var used = writeColumns().length;
      return {
        title: "字段映射", ico: "⇄", cls: "ico-blue",
        state: total ? used + " / " + total : "待探测", st: "ok",
        lines: [
          ["映射方式", TASK.columns.length ? "手工选中 " + TASK.columns.length + " 列" : "全部列（同名同序）"],
          ["写入列数", used + " 列"],
          ["顺序", "按源端顺序写入目标表"]
        ]
      };
    }
    if (type === "mode") {
      var ms = MODE_STATE[TASK.syncMode] || MODE_STATE.full;
      var third = ["预计耗时", "约 " + estimateSecs() + " 秒（按 21,470 行/秒估算）"];
      if (TASK.syncMode === "incremental") {
        third = ["水位列", TASK.watermarkColumn || "尚未指定"];
      } else if (TASK.syncMode === "upsert") {
        third = ["业务键", TASK.keyColumns.length ? TASK.keyColumns.join("、") : "尚未指定"];
      }
      return {
        title: "同步模式", ico: "◈", cls: "ico-amber", state: ms.text, st: ms.st,
        lines: [["同步模式", modeLabel(TASK.syncMode)], ["写入方式", WRITE_SHORT[TASK.syncMode]], third]
      };
    }
    if (type === "dst") {
      var target = TASK.targetTable || TASK.sourceTable || "未填写";
      return {
        title: "目标", ico: "✓", cls: "ico-green",
        state: TASK.targetConnectionId ? target : "待配置", st: TASK.targetConnectionId ? "ok" : "warn",
        lines: [
          ["目标库", TASK.targetConnectionId ? connLabel(TASK.targetConnectionId) : "未选择"],
          ["写入方式", WRITE_SHORT[TASK.syncMode] + " · 每批 " + fmt(TASK.batchRows) + " 行"],
          ["建表策略", "不存在则按源结构创建"]
        ]
      };
    }
    if (type === "skip") {
      var live = TASK.syncMode === "incremental";
      return {
        title: "本轮跳过", ico: "∅", cls: "ico-gray", state: live ? "会触发" : "不会触发",
        st: live ? "" : "warn",
        lines: [
          ["触发条件", "增量模式下水位之后没有新行"],
          ["结果", "不写目标，只记一条日志"],
          ["当前状态", live ? "当前是增量模式，这条分支可能走到" : "当前是「" + modeLabel(TASK.syncMode) + "」，不会触发"]
        ]
      };
    }
    return { title: "节点", ico: "■", cls: "ico-gray", state: "待配置", st: "", lines: [] };
  }

  function paintCard(id, spec) {
    var n = el(id);
    var ico = n.querySelector(".n-ico");
    ico.className = "n-ico " + spec.cls;
    ico.textContent = spec.ico;
    n.querySelector(".n-title").textContent = spec.title;
    var st = n.querySelector(".n-state");
    st.className = "n-state" + (spec.st ? " " + spec.st : "");
    st.textContent = spec.state;
    n.querySelector(".n-lines").innerHTML = spec.lines.map(function (p) {
      return '<div class="n-line"><b>' + esc(p[0]) + "</b>" + esc(p[1]) + "</div>";
    }).join("");
  }
  function repaintAll() {
    ids().forEach(function (id) { paintCard(id, cardOf(id)); });
    drawEdges();
    paintBar();
  }
  // 兜底自检：正常必须为空数组，非空即说明界面出现了自相矛盾的组合。
  function checkConsistency() {
    var bad = [], names = srcColumnNames();
    // 画布上被「−」删掉的必需节点，要在这里拦下来，否则保存时后端才报"请填写目标表名"
    REQUIRED_NODES.forEach(function (k) {
      if (!NODES[k]) bad.push("画布上缺少「" + KINDS[k].title + "」节点，流程跑不起来");
    });
    if (TASK.syncMode !== "incremental" && TASK.watermarkColumn) {
      bad.push("非增量模式却设置了水位列：" + TASK.watermarkColumn);
    }
    if (TASK.syncMode !== "upsert" && TASK.keyColumns.length) {
      bad.push("非 upsert 模式却设置了业务键：" + TASK.keyColumns.join("、"));
    }
    if (TASK.watermarkColumn && names.length && names.indexOf(TASK.watermarkColumn) < 0) {
      bad.push("水位列不在当前源端列里：" + TASK.watermarkColumn);
    }
    if (TASK.sourceConnectionId && TASK.sourceConnectionId === TASK.targetConnectionId &&
      (TASK.targetTable || TASK.sourceTable) === TASK.sourceTable) {
      bad.push("源表与目标表在同一个连接且同名（自己同步给自己）");
    }
    TASK.keyColumns.forEach(function (k) {
      if (writeColumns().indexOf(k) < 0) bad.push("业务键「" + k + "」不在写入的列里");
    });
    return bad;
  }
  window.__taskConsistency = checkConsistency;
  window.__task = TASK;
  // 走查用的只读出口：当前画布上有哪些节点、连线是怎么连的
  window.__flow = function () {
    return {
      nodes: KIND_ORDER.filter(function (k) { return !!NODES[k]; }),
      edges: EDGES.map(function (e) { return e.from + "→" + e.to; })
    };
  };

  /* ---------------------------------------------------------------- 表单零件 */
  function row(label, body, req) {
    return '<div class="frm-row"><div class="frm-lab' + (req ? " req" : "") + '">' + esc(label) +
      "</div><div class=\"frm-ctl\">" + body + "</div></div>";
  }
  function sec(title, body, off, note) {
    return '<div class="frm-sec' + (off ? " off" : "") + '"><h4>' + esc(title) + "</h4>" +
      body + (note ? '<p class="hint">' + note + "</p>" : "") + "</div>";
  }
  function optionsHtml(list, cur) {
    return list.map(function (it) {
      var val = it.value === undefined ? it : it.value;
      var text = it.label === undefined ? it : it.label;
      return '<option value="' + esc(val) + '"' + (String(val) === String(cur) ? " selected" : "") +
        ">" + esc(text) + "</option>";
    }).join("");
  }
  function modeCards() {
    return '<div class="opts" data-group="syncMode">' + STATE.modes.map(function (m) {
      var tag = !m.ready ? '<span class="opt-tag lim">未开放</span>'
        : (m.value === "full" ? '<span class="opt-tag rec">推荐</span>' : "");
      return '<div class="opt' + (m.value === TASK.syncMode ? " on" : "") +
        (m.ready ? "" : " disabled") + '" data-val="' + esc(m.value) + '">' +
        '<div class="opt-t">' + esc(m.label) + tag + "</div>" +
        '<div class="opt-d">' + esc(m.description) + "</div></div>";
    }).join("") + "</div>";
  }
  function columnChecks(group, selected) {
    var names = srcColumnNames();
    if (!names.length) {
      return '<p class="hint">还没有探测到源端列。请先在「源」节点选好连接与表。</p>';
    }
    return '<div class="cols">' + names.map(function (name) {
      var meta = (STATE.probe.columns || []).filter(function (c) { return c.name === name; })[0] || {};
      var on = selected.indexOf(name) >= 0;
      return '<label class="colrow chk' + (on ? " on" : "") + '" data-group="' + group +
        '" data-val="' + esc(name) + '"><i></i>' +
        '<span class="nm">' + esc(name) + '</span><span class="ty">' + esc(meta.type || "") +
        "</span></label>";
    }).join("") + "</div>";
  }

  /* ---------------------------------------------------------------- 面板 */
  function panelHTML(type) {
    if (type === "src") {
      return sec("节点类型", '<p class="hint">这是流程的第 1 步：从源端把数据读出来。</p>') +
        // 任务名与启用状态是**任务级**的东西，但流程的起点就是源节点，
        // 放在这里最符合直觉（业主：「画布的名称应该在第 1 个节点里有字段体现」）。
        sec("任务",
          row("任务名称", '<input class="inp" data-field="name" placeholder="例如：会员小票表 → 分析库" value="' +
            esc(TASK.name) + '" />', true) +
          row("启用", '<label class="chk' + (TASK.enabled ? " on" : "") +
            '" data-toggle="enabled"><i></i>启用这个同步任务</label>' +
            '<p class="hint">停用只是标记「暂时不用」，配置照旧保留；列表里会标注「已停用」。</p>'),
          false, '<p class="hint">任务名会显示在列表和画布顶部 —— 取一个一眼能看出源与目标的。</p>') +
        sec("数据源",
          row("源连接", '<select class="sel" data-field="sourceConnectionId">' +
            '<option value="">请选择…</option>' +
            optionsHtml(STATE.connections.map(function (c) {
              return { value: c.id, label: c.name + " · " + (c.database || "") };
            }), TASK.sourceConnectionId) + "</select>", true) +
          row("取数方式", '<div class="seg2" data-group="sourceMode">' +
            '<button type="button" class="' + (TASK.sourceMode === "table" ? "on" : "") +
            '" data-val="table">整表</button>' +
            '<button type="button" class="' + (TASK.sourceMode === "sql" ? "on" : "") +
            '" data-val="sql">自定义 SQL</button></div>') +
          (TASK.sourceMode === "table"
            ? row("源表", '<select class="sel" data-field="sourceTable">' +
              '<option value="">请选择…</option>' + optionsHtml(STATE.srcTables, TASK.sourceTable) +
              "</select>", true)
            : row("源端 SQL", '<textarea class="inp" data-field="sourceSql" ' +
              'placeholder="select 列 from 表 where 条件">' + esc(TASK.sourceSql) + "</textarea>", true) +
            '<p class="hint warn">源端只能是<b>一条只读语句</b>（select / with）；带写入或多个语句会被拒绝。</p>'),
          false, '<p class="hint">候选值全部来自你选的连接，列清单在选定表后由服务端探测。</p>');
    }

    if (type === "map") {
      var all = srcColumnNames();
      return sec("节点类型", '<p class="hint">第 2 步：决定把源端的哪些列写进目标表。</p>') +
        sec("列选择",
          row("同步哪些列", columnChecks("columns", TASK.columns)) +
          row("", '<div class="inline"><button type="button" class="dw-btn" id="pickAll">全选</button>' +
            '<button type="button" class="dw-btn" id="pickNone">清空</button>' +
            '<span class="unit">清空 = 全部列（同名同序写入）</span></div>'),
          all.length ? false : true,
          '<p class="hint">顺序沿用源端顺序。目标表缺列会在预览阶段直接拦下。</p>');
    }

    if (type === "mode") {
      var isInc = TASK.syncMode === "incremental";
      var isUpsert = TASK.syncMode === "upsert";
      var timeish = srcColumnNames().filter(function (n) {
        return /时间|日期|time|date/i.test(n);
      });
      var body = modeCards();
      if (isInc) {
        body += '<div class="frm-sec"><h4>增量设置</h4>' +
          row("水位列", '<select class="sel" data-field="watermarkColumn">' +
            '<option value="">请选择…</option>' +
            optionsHtml(srcColumnNames(), TASK.watermarkColumn) + "</select>", true) +
          (timeish.length
            ? '<p class="hint">该表里含时间的列：' + esc(timeish.slice(0, 6).join("、")) + '</p>'
            : '<p class="hint warn">没找到含时间的列。没有「行修改时间」的表用增量会漏掉改动过的行，建议改用全量覆盖。</p>') +
          "</div>";
      }
      if (isUpsert) {
        body += '<div class="frm-sec"><h4>业务键</h4>' +
          row("键列", columnChecks("keyColumns", TASK.keyColumns), true) +
          '<p class="hint">键列必须在上面「字段映射」里保留，否则执行会被拒绝。目标表不需要有唯一索引。</p>' +
          "</div>";
      }
      if (STATE.probe && (STATE.probe.warnings || []).length) {
        body += sec("这张表的提示", '<ul style="margin:0;padding-left:18px">' +
          STATE.probe.warnings.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul>");
      }
      return sec("节点类型", '<p class="hint">第 3 步：同一份源数据，四档模式写出来的结果完全不同。</p>') + body;
    }

    if (type === "dst") {
      return sec("节点类型", '<p class="hint">第 4 步：写到哪个库的哪张表。</p>') +
        sec("目标",
          row("目标连接", '<select class="sel" data-field="targetConnectionId">' +
            '<option value="">请选择…</option>' +
            optionsHtml(STATE.connections.map(function (c) {
              return { value: c.id, label: c.name + " · " + (c.database || "") };
            }), TASK.targetConnectionId) + "</select>", true) +
          row("目标表", '<input class="inp" list="dstTableList" data-field="targetTable" value="' +
            esc(TASK.targetTable) + '" placeholder="' + esc(TASK.sourceTable || "目标表名") + '" />' +
            '<datalist id="dstTableList">' + optionsHtml(STATE.dstTables) + "</datalist>") +
          '<p class="hint">留空则沿用源表名。表不存在时按源表结构自动创建；已存在但缺列不会自动改结构。</p>' +
          '<div class="locked">写入方式由<b>同步模式</b>决定，这里不能改，避免出现互相矛盾的配置：<br>' +
          esc(WRITE_LONG[TASK.syncMode]) + "</div>") +
        sec("写入选项",
          row("每批行数", '<input class="inp sm" type="number" min="100" max="' + STATE.maxBatchRows +
            '" data-field="batchRows" value="' + TASK.batchRows + '" /><span class="unit">行（100 ~ ' +
            fmt(STATE.maxBatchRows) + "）</span>") +
          row("提交方式", '<select class="sel" data-field="commitMode">' +
            optionsHtml([{ value: "batch", label: "每批提交（推荐，失败只丢最后一批）" },
              { value: "all", label: "全部一起提交（更慢，但完全不丢中间批次）" }], TASK.commitMode) + "</select>") +
          row("遇错停止", '<label class="chk' + (TASK.stopOnError ? " on" : "") +
            '" data-toggle="stopOnError"><i></i>遇到错误立即停止</label>'));
    }

    if (type === "skip") {
      var live = TASK.syncMode === "incremental";
      return sec("节点类型", '<p class="hint">增量模式下「没有新数据」的分支。</p>') +
        sec("这条分支什么时候走到",
          '<div class="locked">' +
          (live
            ? "当前是<b>按时间戳增量</b>：若水位之后没有新行，本轮不写目标表，只记一条日志。<br>这不是失败 —— 是「没有要同步的东西」。"
            : "当前是<b>" + esc(modeLabel(TASK.syncMode)) + "</b>，每轮都会读源表全量，这条分支不会触发。") +
          "</div>") +
        sec("需要注意的地方",
          '<p class="hint warn">增量只认「水位之后新出现的行」。已经存在的行事后被修改过，不会重来一遍。<br>' +
          "多次重跑不会因为跳过而丢数据 —— 水位只在**成功**那一轮才前进。</p>");
    }
    return "";
  }

  /* ---------------------------------------------------------------- 抽屉 */
  /* 面板宽 632px，**盖在画布上**：开合都不动画布的平移与缩放（业主明确要求
     "点开配置时画布保持原状大小，不用考虑遮不遮住节点"）。
     所以这里既不 fitView，也不平移 —— 挪了反而会让用户正在看的节点跑掉。 */
  function openDrawer(id) {
    openId = id;
    var spec = cardOf(id);
    ids().forEach(function (n) { el(n).classList.toggle("selected", n === id); });
    el("dwIco").className = "dw-ico " + spec.cls;
    el("dwIco").textContent = spec.ico;
    el("dwTitle").textContent = spec.title;
    el("dwSub").textContent = el(id).dataset.type;
    drawerBody.innerHTML = panelHTML(el(id).dataset.type);
    drawer.classList.add("open");
    setTimeout(function () { drawEdges(); }, 230);
  }
  function closeDrawer() {
    openId = null;
    drawer.classList.remove("open");
    ids().forEach(function (n) { el(n).classList.remove("selected"); });
    setTimeout(function () { drawEdges(); }, 230);
  }
  el("dwClose").onclick = closeDrawer;
  el("dwCancel").onclick = closeDrawer;
  el("dwOk").onclick = function () { repaintAll(); closeDrawer(); };

  /* 面板内的交互：一律重绘。任何一处改动都让全部卡面重新推导 —— 这是"不会互相违背"的机械保证。 */
  drawerBody.addEventListener("click", function (e) {
    var optNode = e.target.closest(".opt");
    if (optNode && !optNode.classList.contains("disabled")) {
      var group = optNode.parentNode.dataset.group;
      if (group === "syncMode") {
        TASK.syncMode = optNode.dataset.val;
        // 铁律 3：切走就清掉只属于旧模式的字段，避免"带着上一种模式的设置继续跑"。
        if (TASK.syncMode !== "incremental") TASK.watermarkColumn = "";
        if (TASK.syncMode !== "upsert") TASK.keyColumns = [];
      }
      redrawPanel();
      return;
    }
    var segNode = e.target.closest(".seg2 button");
    if (segNode) {
      TASK.sourceMode = segNode.dataset.val;
      STATE.probe = null;
      reloadProbe();
      redrawPanel();
      return;
    }
    var chkNode = e.target.closest(".chk[data-group]");
    if (chkNode) {
      var key = chkNode.dataset.group, val = chkNode.dataset.val;
      var list = key === "keyColumns" ? TASK.keyColumns : TASK.columns;
      var at = list.indexOf(val);
      if (at >= 0) list.splice(at, 1); else list.push(val);
      redrawPanel();
      return;
    }
    if (e.target.closest('[data-toggle="stopOnError"]')) {
      TASK.stopOnError = !TASK.stopOnError;
      redrawPanel();
      return;
    }
    if (e.target.closest('[data-toggle="enabled"]')) {
      TASK.enabled = !TASK.enabled;
      redrawPanel();
      return;
    }
    if (e.target.id === "pickAll") {
      TASK.columns = srcColumnNames().slice();
      redrawPanel();
      return;
    }
    if (e.target.id === "pickNone") {
      TASK.columns = [];
      redrawPanel();
    }
  });
  drawerBody.addEventListener("change", function (e) {
    var field = e.target.dataset.field;
    if (!field) return;
    if (field === "batchRows") {
      TASK.batchRows = Math.max(100, Math.min(STATE.maxBatchRows, parseInt(e.target.value, 10) || STATE.defaultBatchRows));
    } else {
      TASK[field] = e.target.value;
    }
    if (field === "sourceConnectionId") {
      STATE.srcTables = [];
      if (TASK.sourceMode === "table") TASK.sourceTable = "";
      STATE.probe = null;
      loadTables(TASK.sourceConnectionId, true);
    }
    if (field === "sourceTable") STATE.probe = null;
    if (field === "targetConnectionId") {
      STATE.dstTables = [];
      loadTables(TASK.targetConnectionId, false);
    }
    if (field === "sourceConnectionId" || field === "sourceTable" || field === "sourceSql") {
      reloadProbe();
    }
    redrawPanel();
  });
  // ⚠️ 输入过程中**不要重绘**：重绘会重建下拉/输入框，正在操作的节点当场被销毁，
  // 于是紧接着的 change 事件落在游离节点上、冒泡不到 drawerBody —— 表现就是
  // "选了连接却加载不出表"。重绘统一交给 change（下拉切换 / 输入框失焦）。
  drawerBody.addEventListener("input", function (e) {
    var field = e.target.dataset ? e.target.dataset.field : "";
    if (!field || field === "batchRows") return;
    TASK[field] = e.target.value;
  });

  function redrawPanel() {
    if (!openId) return;
    drawerBody.innerHTML = panelHTML(el(openId).dataset.type);
    repaintAll();
  }

  /* ---------------------------------------------------------------- 接口 */
  function dcRequestHint(method, url, status, hint) {
    return method + " " + url + " 返回 HTTP " + status + "，内容不是 JSON。" + (hint || "");
  }

  function requestJson(url, options) {
    var opts = options || {};
    opts.headers = Object.assign(
      { "Content-Type": "application/json", "X-DC-Request": "1", Accept: "application/json" },
      opts.headers || {}
    );
    return fetch(url, opts).then(function (response) {
      return response.text().then(function (text) {
        var payload = null;
        try { payload = JSON.parse(text); } catch (err) { payload = null; }
        if (!payload) {
          // 非 JSON 基本只有两个来源：服务端还是旧版本（没这个接口 → 404 HTML），
          // 或请求被别的服务挡了。只说"不是 JSON"用户无从下手，得把方法与状态码摆出来。
          var hint = response.status === 404
            ? "（接口不存在 —— 多半是导表工具还在跑旧版本，关掉那个黑窗口重新双击「启动导表工具.bat」）"
            : "";
          throw new Error(dcRequestHint(opts.method || "GET", url, response.status, hint));
        }
        if (payload.ok === false) throw new Error(payload.error || "请求失败。");
        return payload;
      });
    });
  }
  function postJson(url, body) {
    return requestJson(url, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function loadTables(connId, isSource) {
    if (!connId) return Promise.resolve();
    return postJson("/api/tables", { connectionId: connId, targetDbType: "mysql" }).then(function (payload) {
      if (isSource) STATE.srcTables = payload.tables || [];
      else STATE.dstTables = payload.tables || [];
      redrawPanel();
    }).catch(function (err) {
      showDialog("读取表列表失败", esc(err.message));
    });
  }

  function reloadProbe() {
    if (!TASK.sourceConnectionId) { STATE.probe = null; repaintAll(); return Promise.resolve(); }
    if (TASK.sourceMode === "sql" && !TASK.sourceSql.trim()) { STATE.probe = null; repaintAll(); return Promise.resolve(); }
    if (TASK.sourceMode === "table" && !TASK.sourceTable) { STATE.probe = null; repaintAll(); return Promise.resolve(); }
    return postJson("/api/sync/probe", {
      sourceConnectionId: TASK.sourceConnectionId,
      sourceMode: TASK.sourceMode,
      sourceTable: TASK.sourceTable,
      sourceSql: TASK.sourceSql
    }).then(function (payload) {
      STATE.probe = payload.probe;
      // 列集合变了，旧的选择必须收敛到仍然存在的列，否则会带着不存在的列往下走。
      var names = srcColumnNames();
      TASK.columns = TASK.columns.filter(function (c) { return names.indexOf(c) >= 0; });
      TASK.keyColumns = TASK.keyColumns.filter(function (c) { return names.indexOf(c) >= 0; });
      if (names.indexOf(TASK.watermarkColumn) < 0) TASK.watermarkColumn = "";
      repaintAll();
    }).catch(function (err) {
      STATE.probe = null;
      repaintAll();
      showDialog("探测源端失败", esc(err.message));
    });
  }

  function taskPayload() {
    return {
      id: TASK.id,
      name: TASK.name,
      sourceConnectionId: TASK.sourceConnectionId,
      sourceMode: TASK.sourceMode,
      sourceTable: TASK.sourceTable,
      sourceSql: TASK.sourceSql,
      targetConnectionId: TASK.targetConnectionId,
      targetTable: TASK.targetTable || TASK.sourceTable,
      columns: TASK.columns,
      syncMode: TASK.syncMode,
      keyColumns: TASK.keyColumns,
      watermarkColumn: TASK.watermarkColumn,
      batchRows: TASK.batchRows,
      commitMode: TASK.commitMode,
      stopOnError: TASK.stopOnError,
      enabled: TASK.enabled
    };
  }

  function guardBeforeAction() {
    var bad = checkConsistency();
    if (bad.length) { showDialog("配置还不能用", bad.map(function (b) { return "· " + esc(b); }).join("<br>")); return false; }
    if (!TASK.sourceConnectionId || !TASK.targetConnectionId) {
      showDialog("配置还不能用", "请先选好源连接与目标连接。"); return false;
    }
    if (TASK.sourceMode === "table" && !TASK.sourceTable) {
      showDialog("配置还不能用", "请先选择源表。"); return false;
    }
    if (TASK.sourceMode === "sql" && !TASK.sourceSql.trim()) {
      showDialog("配置还不能用", "请先填写源端 SQL。"); return false;
    }
    return true;
  }

  function showDialog(title, html) {
    el("dlgTitle").textContent = title;
    el("dlgBody").innerHTML = html;
    el("syncDialog").showModal();
  }
  function linesHtml(pairs) {
    return pairs.filter(function (p) { return p[1] !== "" && p[1] !== null && p[1] !== undefined; })
      .map(function (p) { return '<div class="ln"><span>' + esc(p[0]) + "</span><span>" + p[1] + "</span></div>"; })
      .join("");
  }
  el("dlgClose").onclick = function () { el("syncDialog").close(); };

  el("btnPreview").onclick = function () {
    if (!guardBeforeAction()) return;
    setBusy(true, "正在预演…");
    postJson("/api/sync/preview", taskPayload()).then(function (payload) {
      var pv = payload.preview;
      STATE.preview = pv;
      var head = pv.canRun
        ? '这一跑会发生什么（<b style="color:#4a9d4a">可以直接执行</b>）：'
        : '这一跑会发生什么（<b style="color:#c0504d">有拦路问题，先修好再执行</b>）：';
      var body = head + "<br>" + pv.plan.map(function (p) { return "· " + esc(p); }).join("<br>");
      if ((pv.blockers || []).length) {
        body += '<br><br><b style="color:#c0504d">拦路问题</b><ul>' +
          pv.blockers.map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul>";
      }
      if ((pv.warnings || []).length) {
        body += '<br><b style="color:#c9932b">提醒</b><ul>' +
          pv.warnings.map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("") + "</ul>";
      }
      if (pv.createTableHint) {
        body += '<br><b>可先在目标库执行这份草稿（类型仅为推断，请核对）</b><pre style="white-space:pre-wrap;background:#f7f8fa;padding:10px;border-radius:6px">'
          + esc(pv.createTableHint) + "</pre>";
      }
      showDialog("任务预览", body);
    }).catch(function (err) {
      showDialog("任务预览失败", esc(err.message));
    }).then(function () { setBusy(false); });
  };

  el("btnRun").onclick = function () {
    if (!guardBeforeAction()) return;
    var pv = STATE.preview;
    if (pv && pv.canRun === false) {
      showDialog("还不能执行", "上一次预览发现拦路问题，请先在「任务预览」里修好：<br><br>" +
        pv.blockers.map(function (b) { return "· " + esc(b); }).join("<br>"));
      return;
    }
    if (!window.confirm("即将按当前配置执行同步，会写入目标表。确定继续？")) return;
    setBusy(true, "正在同步…");
    postJson("/api/sync/run", taskPayload()).then(function (payload) {
      var run = payload.run;
      showDialog("同步完成",
        '<div class="ln"><span>模式</span><span>' + esc(run.syncModeLabel) + "</span></div>" +
        linesHtml([
          ["读取", fmt(run.rowsRead) + " 行"],
          ["新增", fmt(run.rowsWritten) + " 行"],
          ["更新", fmt(run.rowsUpdated) + " 行"],
          ["目标表", esc(run.targetLabel) + " · 合计 " + fmt(run.targetRows) + " 行"],
          ["耗时", (run.elapsedMs / 1000).toFixed(1) + " 秒"],
          ["水位", run.watermarkFrom || run.watermarkTo
            ? esc(run.watermarkFrom || "（空）") + " → " + esc(run.watermarkTo || "（无）") : ""]
        ]) + ((run.notes || []).length
          ? "<br>备注<ul>" + run.notes.map(function (n) { return "<li>" + esc(n) + "</li>"; }).join("") + "</ul>"
          : ""));
      STATE.preview = null;
    }).catch(function (err) {
      showDialog("同步失败", esc(err.message));
    }).then(function () { setBusy(false); });
  };

  el("btnSave").onclick = function () {
    if (!guardBeforeAction()) return;
    if (!TASK.name.trim()) {
      el("nameInput").value = "";
      el("nameDialog").showModal();
      return;
    }
    saveNow();
  };
  function saveNow() {
    TASK.name = el("nameInput").value.trim() || TASK.name;
    if (!TASK.name.trim()) { showDialog("保存失败", "请填写任务名称。"); return; }
    el("nameDialog").close();
    setBusy(true, "正在保存…");
    postJson("/api/sync/tasks", taskPayload()).then(function (payload) {
      TASK.id = payload.task.id;
      TASK.name = payload.task.name;
      markSaved();
      if (typeof window.SyncCanvas.onSaved === "function") window.SyncCanvas.onSaved(payload.task);
      showDialog("已保存", "同步任务已保存：<b>" + esc(payload.task.name) + "</b><br>" +
        "可以继续点「立即同步」，或点左上角「← 同步任务」回到列表。");
    }).catch(function (err) {
      showDialog("保存失败", esc(err.message));
    }).then(function () { setBusy(false); });
  }
  el("nameOk").onclick = saveNow;
  el("nameCancel").onclick = function () { el("nameDialog").close(); };
  el("nameClose").onclick = function () { el("nameDialog").close(); };
  el("nameInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); saveNow(); }
  });

  function setBusy(busy, text) {
    ["btnPreview", "btnSave", "btnRun"].forEach(function (id) {
      el(id).disabled = busy;
      if (!busy) el(id).textContent = { btnPreview: "任务预览", btnSave: "保存任务", btnRun: "立即同步" }[id];
    });
    if (busy) el("btnRun").textContent = text;
  }

  /* ---------------------------------------------------------------- 顶部条 */
  function paintBar() {
    el("cbName").textContent = TASK.name || "未命名任务";
    var flag = el("cbFlag");
    if (!TASK.id) { flag.textContent = "新建 · 尚未保存"; flag.className = "cb-flag new"; return; }
    var dirty = isDirty();
    flag.textContent = dirty ? "已修改 · 未保存" : "已保存";
    flag.className = "cb-flag " + (dirty ? "dirty" : "saved");
  }
  function isDirty() {
    return !!TASK.id && JSON.stringify(taskPayload()) !== STATE.savedSnapshot;
  }
  function markSaved() {
    STATE.savedSnapshot = JSON.stringify(taskPayload());
    paintBar();
  }

  /* ---------------------------------------------------------------- 对外接口 */
  /* 画布是**同步模块里的第二个视图**（不是独立页面）——
     由 sync.js 负责显示/隐藏，这里只负责"装哪个任务"和"画成什么样"。 */
  var ready = Promise.all([
    requestJson("/api/connections").then(function (payload) { STATE.connections = payload.connections || []; }),
    requestJson("/api/sync/tasks").then(function (payload) {
      STATE.modes = payload.modes || [];
      STATE.defaultBatchRows = payload.defaultBatchRows || 5000;
      STATE.maxBatchRows = payload.maxBatchRows || 50000;
    })
  ]).catch(function (err) {
    var msg = err.message || "";
    showDialog(msg.indexOf("HTTP 404") >= 0 ? "服务端版本过旧" : "加载失败", esc(msg));
  });

  /* 把一个任务装进画布。t 为空 = 新建空白任务。
     ⚠️ 必须先把 STATE.probe / STATE.preview 清掉：它们是**上一个任务**的探测结果，
     不清就会带着别人的列清单去校验水位列和业务键，checkConsistency() 还查不出来。 */
  function adoptTask(t) {
    TASK = blankTask();
    if (t) {
      TASK.id = t.id || "";
      TASK.name = t.name || "";
      TASK.sourceConnectionId = t.sourceConnectionId || "";
      TASK.sourceMode = t.sourceMode || "table";
      TASK.sourceTable = t.sourceTable || "";
      TASK.sourceSql = t.sourceSql || "";
      TASK.targetConnectionId = t.targetConnectionId || "";
      TASK.targetTable = t.targetTable || "";
      TASK.columns = t.columns || [];
      TASK.syncMode = t.syncMode || "full";
      TASK.keyColumns = t.keyColumns || [];
      TASK.watermarkColumn = t.watermarkColumn || "";
      TASK.batchRows = t.batchRows || STATE.defaultBatchRows;
      TASK.commitMode = t.commitMode || "batch";
      TASK.stopOnError = !!t.stopOnError;
      TASK.enabled = t.enabled === undefined ? true : !!t.enabled;
    }
    STATE.probe = null;
    STATE.preview = null;
    // 画布上的节点随任务重建：新建只有起点，打开已存任务摆出它配过的那些
    resetFlow(t);
    closeDrawer();
    // 刚落库的配置就是"干净"的，之后任何改动都会被算成未保存
    STATE.savedSnapshot = t ? JSON.stringify(taskPayload()) : "";
    repaintAll();
    if (t) {
      if (TASK.sourceConnectionId) loadTables(TASK.sourceConnectionId, true).then(reloadProbe);
      if (TASK.targetConnectionId) loadTables(TASK.targetConnectionId, false);
    }
  }

  /* 供 sync.js（第一层）调用。画布自己不认识"列表""返回"，那些是模块层的事。 */
  window.SyncCanvas = {
    ready: ready,
    load: function (task) {
      return ready.then(function () {
        adoptTask(task || null);
        fitView();
      });
    },
    isDirty: isDirty,
    currentName: function () { return TASK.name; },
    currentId: function () { return TASK.id; },
    onSaved: null // sync.js 挂上去，保存成功后刷新列表
  };

  window.addEventListener("resize", function () { drawEdges(); });
  /* 默认只放起点 —— 业主要求"不要一上来就把 5 个节点全铺出来"，
     后续节点由用户点「＋」按需添加。 */
  resetFlow(null);
  repaintAll();
})();
