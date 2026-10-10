/* /admin:新鲜度 / 任务 / 配额 / 数据源管理 / 手动同步。13F 部分已迁 /funds 页。*/
"use strict";
const $ = (s) => document.querySelector(s);

let sourceState = null;     // {chains: {domain: [provider...]}, providers, ...}

async function load() {
  const d = await fetch("/api/admin/status").then(r => r.json());
  if (!d.ok) return;
  // 新鲜度:红(>5天/无) 黄(今天之前2天内) 绿(今日更新)
  const rows = Object.entries(d.freshness || {}).map(([t, last]) => {
    const isToday = last === new Date().toISOString().slice(0, 10);
    const stale = !last || (Date.now() - new Date(last)) / 864e5 > 5;
    const cls = !last ? "❌" : isToday ? "✅" : stale ? "⚠️" : "🟡";
    return `<tr><td>${t}</td><td>${last || "无数据"}</td><td>${cls}</td></tr>`;
  }).join("");
  $("#freshness").innerHTML = `<table><thead><tr><th>标的</th><th>本地最后日期</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table>`;
  // 任务
  const jobs = (d.jobs || []).map(j =>
    `<div class="event-row"><span style="min-width:150px">${j.id}</span><span>${j.next_run || "未排期"}</span></div>`).join("");
  $("#jobs").innerHTML = jobs || "<p class='hint'>调度未运行</p>";
  // 配额
  const q = d.fmp_quota || {};
  const pct = Math.min((q.used || 0) / (q.limit || 250) * 100, 100);
  $("#quota-bar").style.width = pct + "%";
  $("#quota-txt").textContent = `${q.used || 0} / ${q.limit}（缓存命中不计数）`;
  // TQQQ verify
  try {
    const v = d.tqqq_verify ? JSON.parse(d.tqqq_verify) : null;
    $("#verify").innerHTML = v
      ? `${v.ok ? "✅" : "❌"} ${v.at} · corr ${v.corr ? (v.corr).toFixed(4) : "—"} · 漂移 ${v.drift != null ? (v.drift * 100).toFixed(2) + "%" : "—"}`
      : "尚无对账记录（月度1日自动/手动触发）";
  } catch { $("#verify").textContent = d.tqqq_verify || "—"; }
  // 日志
  const logs = (d.sync_log || []).map(l =>
    `<tr><td>${l.dataset}</td><td>${l.status}</td><td>${l.finished_at || ""}</td><td>${(l.message || "").slice(0, 60)}</td></tr>`).join("");
  $("#sync-log").innerHTML = `<table><thead><tr><th>任务</th><th>状态</th><th>完成时间</th><th>信息</th></tr></thead><tbody>${logs}</tbody></table>`;
  loadSources();
}

async function sync(dataset) {
  $("#sync-status").textContent = `已触发 ${dataset}（后台执行，几秒后刷新）`;
  await fetch("/api/admin/sync", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset }) });
  setTimeout(load, 4000);
}

// ── 数据源管理 ─────────────────────────────────────────────

async function loadSources() {
  const d = await fetch("/api/admin/sources").then(r => r.json()).catch(() => null);
  if (!d || !d.ok) { $("#sources").innerHTML = "<p class='hint'>加载失败</p>"; return; }
  sourceState = d;
  const provName = id => (((d.providers || {})[id] || {}).name) || id;
  const provNote = id => (((d.providers || {})[id] || {}).note) || "";
  // 可调优先链(每个域一张表,↑↓排序)
  let html = "";
  for (const [dom, info] of Object.entries(d.domains || {})) {
    html += `<h3>${info.label} <span class="hint">数据域 ${dom}</span></h3>
      <table><thead><tr><th>#</th><th>源</th><th>说明</th><th>调整</th></tr></thead><tbody data-domain="${dom}">` +
      info.chain.map((p, i) => `
        <tr data-provider="${p}">
          <td>${i + 1}</td>
          <td><strong>${provName(p)}</strong> <span class="hint">(${p})</span></td>
          <td class="hint" style="max-width:340px">${provNote(p)}</td>
          <td>
            <button style="padding:2px 8px" onclick="moveChain('${dom}', '${p}', -1)">↑</button>
            <button style="padding:2px 8px" onclick="moveChain('${dom}', '${p}', 1)">↓</button>
          </td></tr>`).join("") +
      `</tbody></table>`;
  }
  // 固定源(单一来源的功能)
  const fixed = (d.fixed || []).map(f =>
    `<tr><td>${f.label}</td><td><strong>${f.provider}</strong></td><td class="hint" style="max-width:360px">${f.note}</td></tr>`).join("");
  html += `<h3>固定单一源的功能</h3>
    <table><thead><tr><th>功能</th><th>源</th><th>说明</th></tr></thead><tbody>${fixed}</tbody></table>`;
  // 每个标的最近一次的实际来源
  const per = (d.per_ticker || []).map(t =>
    `<tr><td>${t.ticker}</td><td>${provName(t.provider)}</td><td class="hint">${(t.at || "").replace("T", " ")}</td></tr>`).join("");
  html += `<h3>各标的最近实际来源</h3>
    <table><thead><tr><th>标的</th><th>最近来源</th><th>记录时间</th></tr></thead><tbody>${per || "<tr><td colspan=3 class='hint'>暂无(下次同步后自动记录)</td></tr>"}</tbody></table>
    <p class="hint">说明:某标的首次成功由链条中靠前的源提供后记录在此;若其数据因频控/启动失败,可调整优先链后再点「同步行情」。</p>`;
  $("#sources").innerHTML = html;
}

function moveChain(domain, provider, dir) {
  if (!sourceState || !sourceState.domains[domain]) return;
  const chain = sourceState.domains[domain].chain;
  const i = chain.indexOf(provider);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= chain.length) return;
  [chain[i], chain[j]] = [chain[j], chain[i]];
  loadSources();     // 重渲反映新顺序(保存前仅内存态)
}

async function saveChains() {
  if (!sourceState) return;
  const st = $("#sources-status");
  let saved = 0, failed = [];
  for (const [dom, info] of Object.entries(sourceState.domains || {})) {
    try {
      const res = await fetch("/api/admin/sources", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: dom, chain: info.chain }),
      });
      const d = await res.json();
      if (d.ok) saved++; else failed.push(dom);
    } catch (e) { failed.push(dom); }
  }
  st.textContent = failed.length
    ? `❌ ${saved} 个域已保存;失败: ${failed.join(",")}`
    : `✅ 已保存 ${saved} 个数据域的优先链(下次同步生效)`;
  loadSources();
}

load();
