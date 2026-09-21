"use strict";

const $ = (sel) => document.querySelector(sel);

const els = {
  template: $("#template"),
  gutter: $("#gutter"),
  highlight: $("#highlight-layer"),
  marks: $("#mark-layer"),
  locale: $("#locale"),
  varList: $("#var-list"),
  addVar: $("#btn-add-var"),
  rendered: $("#rendered"),
  btnCopy: $("#btn-copy"),
  btnExport: $("#btn-export"),
  btnExample: $("#btn-example"),
  errorPanel: $("#error-panel"),
  errorList: $("#error-list"),
  warningPanel: $("#warning-panel"),
  warningList: $("#warning-list"),
  branchList: $("#branch-list"),
  resolvedLocale: $("#resolved-locale"),
  coverage: $("#coverage-chip"),
  toast: $("#toast"),
};

const TYPE_LABEL = {
  number: "number 数字",
  string: "string 文本",
  date: "date 日期",
  time: "time 时间",
  datetime: "datetime",
};

const EXAMPLE = `{gender, select,
  female {{name} 将她的 {count, plural,
    one {# 张照片}
    other {# 张照片}}分享给了你}
  male {{name} 将他的 {count, plural,
    one {# 张照片}
    other {# 张照片}}分享给了你}
  other {{name} 分享了 {count, plural,
    one {# 张照片}
    other {# 张照片}}}
}，发布于 {when, date, long}`;

let lastPayload = null;
let varRows = []; // {name, type, value}
let debounceTimer = null;
let lastVarSig = "";

// ---------- 工具 ----------

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function showToast(msg) {
  els.toast.textContent = msg;
  els.toast.classList.remove("hidden");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => els.toast.classList.add("hidden"), 1800);
}

// ---------- 行号 ----------

function updateGutter() {
  const lines = els.template.value.split("\n").length;
  let html = "";
  for (let i = 1; i <= lines; i++) html += i + "\n";
  els.gutter.textContent = html;
  syncScroll();
}

function syncScroll() {
  els.gutter.scrollTop = els.template.scrollTop;
  els.highlight.scrollTop = els.template.scrollTop;
  els.highlight.scrollLeft = els.template.scrollLeft;
  els.marks.scrollTop = els.template.scrollTop;
  els.marks.scrollLeft = els.template.scrollLeft;
}

// ---------- 语法高亮 / 错误标记层 ----------

function renderLayers(payload) {
  const src = els.template.value;

  // 命中的分支键: 蓝色标记
  const hitRanges = [];
  (payload?.branches || []).forEach((b) => {
    if (b.covered) hitRanges.push([b.offset, b.end]);
    // 整体参数区域弱标记
  });

  // 语法高亮: 简单词法扫描, 花括号结构
  const tokenRanges = [];
  let i = 0;
  let depth = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === "'") {
      // 跳过转义
      let j = i + 1;
      while (j < src.length) {
        if (src[j] === "'") {
          if (src[j + 1] === "'") { j += 2; continue; }
          j++; break;
        }
        j++;
      }
      tokenRanges.push([i, j, "quote"]);
      i = j;
      continue;
    }
    if (ch === "{") { tokenRanges.push([i, i + 1, "brace"]); depth++; i++; continue; }
    if (ch === "}") { tokenRanges.push([i, i + 1, "brace"]); depth--; i++; continue; }
    if (ch === "#" && depth > 0) { tokenRanges.push([i, i + 1, "hash"]); i++; continue; }
    i++;
  }

  // 合并: 高亮层只画括号/命中键; 区间可能重叠, 用非重叠打平
  const marks = [];
  tokenRanges.forEach(([s, e, t]) => {
    if (t === "brace" || t === "hash") marks.push([s, e, "brace"]);
  });
  hitRanges.forEach(([s, e]) => marks.push([s, e, "hit"]));
  els.highlight.innerHTML = flattenMarks(src, marks) + "\n";

  // 错误(红)与可定位警告(琥珀)波浪线层
  const spans = [];
  (payload?.errors || []).forEach((e) => spans.push([e.offset, e.end || e.offset + 1, ""]));
  (payload?.warnings || []).forEach((w) => {
    if (w.offset) spans.push([w.offset, w.end || w.offset + 1, "warn"]);
  });
  els.marks.innerHTML = flattenMarks(src, spans, true) + "\n";
}

// 把可能重叠的 [start,end,cls] 区间打平成非重叠 <mark> 序列
function flattenMarks(src, ranges, withIdx = false) {
  // 构造排序事件: [位置, 类型(+1开始/-1结束), 区间序号]
  const ev = [];
  const rangeCls = new Map();
  ranges.forEach(([s0, e0, cls], idx) => {
    const s = Math.min(Math.max(s0, 0), src.length);
    const e = Math.min(Math.max(e0, s + 1), src.length);
    if (s >= e) return;
    rangeCls.set(idx, cls || "");
    ev.push([s, 1, idx], [e, -1, idx]);
  });
  ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]);

  const out = [];
  let cursor = 0;
  const active = new Set();
  for (let k = 0; k < ev.length; ) {
    const pos = ev[k][0];
    if (pos > cursor) {
      const cls = active.size ? rangeCls.get([...active].pop()) : null;
      out.push([cursor, pos, cls]);
    }
    while (k < ev.length && ev[k][0] === pos) {
      const [, type, idx] = ev[k];
      if (type === 1) active.add(idx); else active.delete(idx);
      k++;
    }
    cursor = pos;
  }
  if (cursor < src.length) out.push([cursor, src.length, null]);

  let html = "";
  out.forEach(([s, e, cls]) => {
    const text = escapeHtml(src.slice(s, e));
    if (cls === null || cls === undefined) {
      html += text;
    } else if (withIdx) {
      html += `<mark class="${cls}" data-idx="${s}" tabindex="-1">${text}</mark>`;
    } else {
      html += `<mark class="${cls}">${text}</mark>`;
    }
  });
  return html;
}

// ---------- 变量表 ----------

function syncVarRows(payload) {
  const detected = (payload?.variables || []).map((v) => ({
    name: v.name,
    type: v.type,
  }));
  // 保留用户输入的样例值; 移除模板里不存在的; 新增模板检测到的
  const byName = new Map(varRows.map((r) => [r.name, r]));
  const next = [];
  detected.forEach((d) => {
    const old = byName.get(d.name);
    next.push({
      name: d.name,
      type: d.type,
      value: old ? old.value : "",
    });
  });
  // 用户手动添加但尚未出现在模板中的也保留
  varRows.forEach((r) => {
    if (!detected.find((d) => d.name === r.name) && r.manual) next.push(r);
  });
  varRows = next;
  renderVarRows(detected);
}

function renderVarRows(detected) {
  const known = new Set(detected.map((d) => d.name));
  const typeOf = new Map(detected.map((d) => [d.name, d.type]));
  els.varList.innerHTML = "";
  varRows.forEach((row, idx) => {
    const div = document.createElement("div");
    div.className = "var-row";
    const locked = known.has(row.name);
    const t = typeOf.get(row.name) || row.type || "string";
    const samplePlaceholder =
      t === "number" ? "如 3 / 1,234.5" :
      t === "date" ? "YYYY-MM-DD" :
      t === "time" ? "HH:MM" : "任意文本";
    div.innerHTML = `
      <input class="mono vname" data-k="name" value="${escapeHtml(row.name)}"
        ${locked ? "disabled" : ""} placeholder="变量名" />
      <select data-k="type" ${locked ? "disabled" : ""}>
        <option value="string"${t === "string" ? " selected" : ""}>string</option>
        <option value="number"${t === "number" ? " selected" : ""}>number</option>
        <option value="date"${t === "date" ? " selected" : ""}>date</option>
        <option value="time"${t === "time" ? " selected" : ""}>time</option>
      </select>
      <input class="mono" data-k="value" value="${escapeHtml(row.value)}" placeholder="${samplePlaceholder}" />
      <button class="del" type="button" title="${locked ? "模板使用中的变量" : "删除"}">${locked ? "·" : "✕"}</button>
    `;
    div.querySelectorAll("input,select").forEach((inp) => {
      inp.addEventListener("input", () => {
        const k = inp.dataset.k;
        varRows[idx][k] = k === "type" ? inp.value : inp.value;
        schedulePreview();
      });
    });
    div.querySelector(".del").addEventListener("click", () => {
      if (!locked) {
        varRows.splice(idx, 1);
        schedulePreview();
      }
    });
    els.varList.appendChild(div);
  });
}

function collectSamples() {
  const o = {};
  varRows.forEach((r) => { if (r.name) o[r.name] = r.value; });
  return o;
}

// ---------- 渲染结果 ----------

function renderOutput(payload) {
  els.rendered.textContent = payload.rendered || "";

  els.resolvedLocale.textContent = "locale: " + payload.locale_resolved;
  const { covered, total } = payload.coverage;
  els.coverage.textContent = `分支覆盖 ${covered}/${total}`;
  els.coverage.className = "chip " +
    (total === 0 ? "" : covered === total ? "good" : covered === 0 ? "zero" : "partial");

  // 错误
  if (payload.errors.length) {
    els.errorPanel.classList.remove("hidden");
    els.errorList.innerHTML = "";
    payload.errors.forEach((e) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="loc">${e.line}:${e.col}</span>${escapeHtml(e.message)}`;
      li.addEventListener("click", () => focusOffset(e.offset, e.end));
      els.errorList.appendChild(li);
    });
  } else {
    els.errorPanel.classList.add("hidden");
  }

  // 警告
  if (payload.warnings.length) {
    els.warningPanel.classList.remove("hidden");
    els.warningList.innerHTML = "";
    payload.warnings.forEach((w) => {
      const li = document.createElement("li");
      const loc = w.offset ? `<span class="loc">${w.line}:${w.col}</span>` : "";
      li.innerHTML = loc + escapeHtml(w.message);
      if (w.offset) li.addEventListener("click", () => focusOffset(w.offset, w.end));
      els.warningList.appendChild(li);
    });
  } else {
    els.warningPanel.classList.add("hidden");
  }

  // 分支
  els.branchList.innerHTML = "";
  if (payload.branches.length === 0) {
    const d = document.createElement("div");
    d.className = "hint";
    d.textContent = "模板中暂无 plural/select/selectordinal 分支。";
    els.branchList.appendChild(d);
  }
  payload.branches.forEach((b) => {
    const card = document.createElement("div");
    card.className = "branch " + (b.covered ? "covered" : "uncovered");
    const condHtml = b.conditions.length
      ? b.conditions.map((c, i) =>
          (i ? '<span class="b-cond amp">且</span>' : "") +
          `<span class="b-cond">${escapeHtml(c.label)}</span>`).join("")
      : '<span class="b-cond">顶层文本</span>';
    card.innerHTML = `
      <div class="b-top">
        <span class="b-dot"></span>
        <span class="b-status">${b.covered ? "✓ 已覆盖" : "○ 未覆盖"}</span>
        <span class="b-srcline">第 ${b.line} 行</span>
      </div>
      <div class="b-conds">${condHtml}</div>
      <div class="b-text">${b.text === "" ? "<空分支>" : escapeHtml(b.text)}</div>
    `;
    card.addEventListener("click", () => focusOffset(b.offset, b.end));
    els.branchList.appendChild(card);
  });
}

// ---------- 定位 ----------

function focusOffset(offset, end) {
  const src = els.template.value;
  offset = Math.min(offset, src.length);
  // 计算行列
  const before = src.slice(0, offset);
  const line = before.split("\n").length;
  const lineStart = before.lastIndexOf("\n") + 1;
  const col = offset - lineStart;

  // 近似滚动到行
  const lineHeight = 21;
  els.template.focus();
  els.template.setSelectionRange(offset, Math.max(end || offset + 1, offset + 1));
  const targetTop = (line - 1) * lineHeight - els.template.clientHeight / 3;
  els.template.scrollTop = Math.max(0, targetTop);

  // flash
  const mark = document.querySelector(`#mark-layer mark[data-idx="${offset}"]`);
  if (mark) {
    mark.classList.add("flash");
    setTimeout(() => mark.classList.remove("flash"), 1100);
  }
  showToast(`已定位到第 ${line} 行第 ${col} 列`);
}

// ---------- 请求 ----------

async function requestPreview() {
  const body = {
    template: els.template.value,
    locale: els.locale.value || "en",
    variables: collectSamples(),
  };
  let payload;
  try {
    const res = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    payload = await res.json();
  } catch (e) {
    showToast("请求失败: " + e.message);
    return;
  }
  payload.template_nonempty = body.template.trim() !== "";
  lastPayload = payload;

  syncVarRows(payload);
  renderOutput(payload);
  renderLayers(payload);
}

function schedulePreview() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(requestPreview, 220);
}

// ---------- 复制 / 导出 ----------

async function copyRendered() {
  const text = els.rendered.textContent;
  if (!text) { showToast("当前没有可复制的渲染结果"); return; }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  els.btnCopy.classList.add("ok");
  els.btnCopy.textContent = "✓ 已复制";
  showToast("渲染结果已复制到剪贴板");
  setTimeout(() => {
    els.btnCopy.classList.remove("ok");
    els.btnCopy.textContent = "复制渲染结果";
  }, 1500);
}

async function exportJson() {
  const body = {
    template: els.template.value,
    locale: els.locale.value || "en",
    variables: collectSamples(),
  };
  try {
    const res = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "icu-preview.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast("已导出 icu-preview.json");
  } catch (e) {
    showToast("导出失败: " + e.message);
  }
}

// ---------- 事件 ----------

els.template.addEventListener("input", () => {
  updateGutter();
  schedulePreview();
});
els.template.addEventListener("scroll", syncScroll);
els.template.addEventListener("keydown", (e) => {
  if (e.key === "Tab") {
    e.preventDefault();
    const s = els.template.selectionStart;
    const en = els.template.selectionEnd;
    const v = els.template.value;
    els.template.value = v.slice(0, s) + "  " + v.slice(en);
    els.template.selectionStart = els.template.selectionEnd = s + 2;
    updateGutter();
    schedulePreview();
  }
});
els.locale.addEventListener("input", schedulePreview);
els.addVar.addEventListener("click", () => {
  varRows.push({ name: "", type: "string", value: "", manual: true });
  renderVarRows(lastPayload?.variables || []);
  const first = els.varList.querySelector('input[data-k="name"]:not([disabled])');
  if (first) first.focus();
});
els.btnCopy.addEventListener("click", copyRendered);
els.btnExport.addEventListener("click", exportJson);
els.btnExample.addEventListener("click", () => {
  els.template.value = EXAMPLE;
  els.locale.value = "zh";
  varRows = [
    { name: "gender", type: "string", value: "female" },
    { name: "name", type: "string", value: "小林" },
    { name: "count", type: "number", value: "3" },
    { name: "when", type: "date", value: "2026-09-21" },
  ];
  updateGutter();
  requestPreview();
});

// ---------- 启动 ----------
updateGutter();
requestPreview();
