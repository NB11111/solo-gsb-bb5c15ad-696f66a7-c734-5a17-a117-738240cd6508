/* ICU 文案分支预览器 — 前端逻辑 */
(function () {
  "use strict";

  var tpl = document.getElementById("template");
  var hl = document.getElementById("highlight");
  var localeInput = document.getElementById("locale");
  var messagesEl = document.getElementById("messages");
  var varRowsEl = document.getElementById("varRows");
  var mainResultEl = document.getElementById("mainResult");
  var mainCondsEl = document.getElementById("mainConds");
  var statsEl = document.getElementById("stats");
  var nodesEl = document.getElementById("nodes");
  var pathsEl = document.getElementById("paths");
  var toastEl = document.getElementById("toast");

  var lastResult = null;
  var debounceTimer = null;

  var DEFAULT_TEMPLATE =
    "{gender, select, male{He} female{She} other{They}} has " +
    "{count, plural, =0{no new messages} one{# new message} other{# new messages}}.";

  /* ---------------- 工具 ---------------- */

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(text) {
    toastEl.textContent = text;
    toastEl.classList.add("show");
    setTimeout(function () { toastEl.classList.remove("show"); }, 1600);
  }

  function copyText(text, label) {
    function done() { toast(label || "已复制"); }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); }
      catch (e) { toast("复制失败，请手动复制"); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else {
      fallback();
    }
  }

  function parseValue(raw) {
    var t = raw.trim();
    if (t === "true") return true;
    if (t === "false") return false;
    if (t !== "" && !isNaN(Number(t))) return Number(t);
    return raw;
  }

  function typeLabel(v) {
    if (typeof v === "number") return "数字";
    if (typeof v === "boolean") return "布尔";
    return "字符串";
  }

  /* ---------------- 变量行 ---------------- */

  function addVarRow(name, value) {
    var row = document.createElement("div");
    row.className = "var-row";

    var nameInput = document.createElement("input");
    nameInput.className = "var-name";
    nameInput.placeholder = "变量名";
    nameInput.value = name || "";

    var valueInput = document.createElement("input");
    valueInput.className = "var-value";
    valueInput.placeholder = "样例值";
    valueInput.value = value === undefined ? "" : String(value);

    var typeBadge = document.createElement("span");
    typeBadge.className = "var-type";
    typeBadge.textContent = "字符串";

    var del = document.createElement("button");
    del.className = "var-del";
    del.textContent = "×";
    del.title = "删除该变量";
    del.addEventListener("click", function () {
      row.remove();
      scheduleAnalyze();
    });

    function updateType() {
      typeBadge.textContent = typeLabel(parseValue(valueInput.value));
    }
    valueInput.addEventListener("input", function () { updateType(); scheduleAnalyze(); });
    nameInput.addEventListener("input", scheduleAnalyze);
    updateType();

    row.appendChild(nameInput);
    row.appendChild(valueInput);
    row.appendChild(typeBadge);
    row.appendChild(del);
    varRowsEl.appendChild(row);
  }

  function collectVariables() {
    var vars = {};
    var rows = varRowsEl.querySelectorAll(".var-row");
    for (var i = 0; i < rows.length; i++) {
      var name = rows[i].querySelector(".var-name").value.trim();
      var raw = rows[i].querySelector(".var-value").value;
      if (name) vars[name] = parseValue(raw);
    }
    return vars;
  }

  /* ---------------- 模板高亮 ---------------- */

  function renderHighlight(tokens, errors) {
    var text = tpl.value;
    // 汇总所有染色区间：token 在前，error 覆盖其上
    var spans = [];
    (tokens || []).forEach(function (t) {
      spans.push({ start: t.start, end: t.end, cls: "tok-" + t.cls });
    });
    (errors || []).forEach(function (e) {
      if (e.pos === null || e.pos === undefined) return;
      spans.push({ start: e.pos, end: e.pos + (e.length || 1), cls: "tok-error" });
    });
    if (!text) {
      hl.innerHTML = "\n";
      return;
    }
    // 切分边界
    var cuts = { 0: true };
    cuts[text.length] = true;
    spans.forEach(function (s) {
      cuts[Math.max(0, Math.min(text.length, s.start))] = true;
      cuts[Math.max(0, Math.min(text.length, s.end))] = true;
    });
    var points = Object.keys(cuts).map(Number).sort(function (a, b) { return a - b; });
    var html = "";
    for (var i = 0; i < points.length - 1; i++) {
      var a = points[i], b = points[i + 1];
      var cls = null;
      for (var j = 0; j < spans.length; j++) {
        if (spans[j].start <= a && spans[j].end >= b) {
          if (spans[j].cls === "tok-error") { cls = "tok-error"; break; }
          if (!cls) cls = spans[j].cls;
        }
      }
      var piece = esc(text.slice(a, b));
      html += cls ? '<span class="' + cls + '">' + piece + "</span>" : piece;
    }
    hl.innerHTML = html + "\n";
  }

  tpl.addEventListener("scroll", function () {
    hl.scrollTop = tpl.scrollTop;
    hl.scrollLeft = tpl.scrollLeft;
  });

  /* ---------------- 消息列表 ---------------- */

  function renderMessages(result) {
    messagesEl.innerHTML = "";
    var all = [];
    (result.errors || []).forEach(function (e) { all.push({ kind: "error", label: "错误", data: e }); });
    (result.warnings || []).forEach(function (w) { all.push({ kind: "warning", label: "警告", data: w }); });
    (result.infos || []).forEach(function (i) { all.push({ kind: "info", label: "提示", data: i }); });
    all.forEach(function (item) {
      var d = item.data;
      var div = document.createElement("div");
      div.className = "msg msg-" + item.kind;
      var loc = "";
      if (d.line !== null && d.line !== undefined) {
        loc = "第 " + d.line + " 行 " + d.col + " 列：";
      }
      div.innerHTML = '<span class="tag">' + item.label + "</span><span>" +
        esc(loc + d.message) + "</span>";
      if (d.pos !== null && d.pos !== undefined) {
        div.title = "点击定位到出错位置";
        div.addEventListener("click", function () {
          tpl.focus();
          var end = Math.min(tpl.value.length, d.pos + (d.length || 1));
          tpl.setSelectionRange(d.pos, end);
        });
      }
      messagesEl.appendChild(div);
    });
  }

  /* ---------------- 结果渲染 ---------------- */

  function badge(covered, reachable, coverageAvailable) {
    if (!reachable) return '<span class="badge badge-dead">不可达</span>';
    if (!coverageAvailable) return '<span class="badge badge-dead">覆盖未知</span>';
    return covered
      ? '<span class="badge badge-covered">已覆盖</span>'
      : '<span class="badge badge-uncovered">未覆盖</span>';
  }

  function renderResult(result) {
    lastResult = result;

    renderHighlight(result.tokens, result.errors);
    renderMessages(result);

    // 主渲染
    if (result.main) {
      mainResultEl.textContent = result.main.output || "（空）";
      mainCondsEl.innerHTML = "";
      (result.main.conditions || []).forEach(function (c) {
        var chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = c;
        mainCondsEl.appendChild(chip);
      });
      if (result.main.incomplete) {
        var warn = document.createElement("span");
        warn.className = "chip";
        warn.textContent = "⚠ 分支变量缺失或类型错误，覆盖情况不可判定";
        mainCondsEl.appendChild(warn);
      }
    }

    // 统计
    var st = result.stats || {};
    var statText = "共 " + st.total_paths + " 条路径";
    if (st.truncated) statText += "（仅展示前 " + st.shown_paths + " 条）";
    if (st.coverage_available) {
      statText += "，样例覆盖 " + st.covered_paths + " 条";
    }
    statsEl.textContent = statText;

    // 分支节点
    nodesEl.innerHTML = "";
    if (!result.nodes || !result.nodes.length) {
      nodesEl.innerHTML = '<div class="empty">模板中没有 plural / select 分支节点。</div>';
    }
    (result.nodes || []).forEach(function (node) {
      var card = document.createElement("div");
      card.className = "node-card";
      var kindCls = node.kind === "select" ? "select" : "";
      var html = '<div class="node-head">' +
        '<span class="node-kind ' + kindCls + '">' + esc(node.kind) + "</span>" +
        "<span>变量 " + esc(node.variable) + "</span></div>";
      node.branches.forEach(function (b) {
        var rowCls = "branch-row" + (!b.reachable ? " dead" : (!b.covered ? " uncovered" : ""));
        html += '<div class="' + rowCls + '">' +
          '<span class="branch-sel">' + esc(b.selector) + "</span>" +
          '<span class="branch-cond">' + esc(b.condition) + "</span>" +
          badge(b.covered, b.reachable, st.coverage_available) +
          "</div>";
      });
      card.innerHTML = html;
      nodesEl.appendChild(card);
    });

    // 路径列表
    pathsEl.innerHTML = "";
    if (!result.paths || !result.paths.length) {
      pathsEl.innerHTML = '<div class="empty">没有可枚举的分支路径。</div>';
    }
    (result.paths || []).forEach(function (p) {
      var card = document.createElement("div");
      card.className = "path-card" + (p.covered ? "" : " uncovered");
      var head = document.createElement("div");
      head.className = "path-head";
      var idSpan = document.createElement("span");
      idSpan.className = "path-id";
      idSpan.textContent = "#" + p.id;
      head.appendChild(idSpan);
      p.conditions.forEach(function (c) {
        var chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = c;
        head.appendChild(chip);
      });
      var badgeSpan = document.createElement("span");
      badgeSpan.innerHTML = badge(p.covered, true, st.coverage_available);
      head.appendChild(badgeSpan);
      var copyBtn = document.createElement("button");
      copyBtn.className = "btn btn-small path-copy";
      copyBtn.textContent = "复制";
      copyBtn.addEventListener("click", function () { copyText(p.output, "已复制路径 #" + p.id); });
      head.appendChild(copyBtn);
      var out = document.createElement("div");
      out.className = "path-output";
      out.textContent = p.output;
      card.appendChild(head);
      card.appendChild(out);
      pathsEl.appendChild(card);
    });
  }

  /* ---------------- 分析请求 ---------------- */

  function analyze() {
    var payload = {
      template: tpl.value,
      locale: localeInput.value.trim() || "en",
      variables: collectVariables(),
    };
    fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(renderResult)
      .catch(function () {
        messagesEl.innerHTML =
          '<div class="msg msg-error"><span class="tag">错误</span>' +
          "<span>无法连接服务器</span></div>";
      });
  }

  function scheduleAnalyze() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(analyze, 350);
  }

  /* ---------------- 事件绑定 ---------------- */

  tpl.addEventListener("input", scheduleAnalyze);
  localeInput.addEventListener("input", scheduleAnalyze);

  document.getElementById("btnAddVar").addEventListener("click", function () {
    addVarRow("", "");
  });

  document.getElementById("btnExtract").addEventListener("click", function () {
    if (!lastResult || !lastResult.variables_used) { analyze(); return; }
    var existing = {};
    varRowsEl.querySelectorAll(".var-name").forEach(function (inp) {
      existing[inp.value.trim()] = true;
    });
    var added = 0;
    lastResult.variables_used.forEach(function (name) {
      if (!existing[name]) { addVarRow(name, ""); added++; }
    });
    toast(added ? "已添加 " + added + " 个变量" : "模板变量均已在列表中");
    if (added) scheduleAnalyze();
  });

  document.getElementById("btnCopyMain").addEventListener("click", function () {
    if (lastResult && lastResult.main) copyText(lastResult.main.output, "已复制渲染结果");
  });

  document.getElementById("btnCopyAll").addEventListener("click", function () {
    if (!lastResult || !lastResult.paths) return;
    var text = lastResult.paths.map(function (p) {
      return "#" + p.id + " [" + p.conditions.join("; ") + "]\n" + p.output;
    }).join("\n\n");
    copyText(text, "已复制全部路径");
  });

  document.getElementById("btnExport").addEventListener("click", function () {
    if (!lastResult) return;
    var data = {
      tool: "icu-branch-previewer",
      exported_at: new Date().toISOString(),
      template: tpl.value,
      locale: localeInput.value.trim() || "en",
      variables: collectVariables(),
      result: lastResult,
    };
    var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "icu-branch-preview.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
    toast("已导出 JSON");
  });

  /* ---------------- 初始化 ---------------- */

  tpl.value = DEFAULT_TEMPLATE;
  addVarRow("gender", "female");
  addVarRow("count", "3");
  analyze();
})();
