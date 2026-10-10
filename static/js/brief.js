/* 每日消息中心:财报/IPO/科技大会/投资者日四个分类块(2026-10-10 重组)。
   窗口 = 过去 30 天 + 未来 7 天;历史通过「回填」按钮幂等补库。
   今日板块涨跌已迁出(板块轮动页已有当日板块)。
   无假数据铁律:某分类数据缺失即隐藏,不显示占位。 */
"use strict";

const $ = (s) => document.querySelector(s);

const escBrief = (s) => String(s).replace(/[&<>"]/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtNum = (v) => v == null ? "—" : Number(v).toLocaleString("en-US");

const CATS = [
  { id: "earnings" },
  { id: "ipo" },
  {
    id: "conference",
    emptyNote: "Nasdaq 日历不提供科技大会/峰会事件流,免费源缺位 — 接入后此处自动显示。不造数据。",
  },
  {
    id: "investor_day",
    emptyNote: "Nasdaq 日历不提供投资者日日程,免费源缺位 — 接入后此处自动显示。不造数据。",
  },
];

function dateCmp(a, b) { return String(a).localeCompare(String(b)); }

function renderCategory(catId, items, emptyNote) {
  const box = $("#ev-" + catId);
  const panelTitle = box.closest(".panel");
  const cnt = $("#cnt-" + catId);
  const today = new Date().toISOString().slice(0, 10);
  if (!items.length) {
    panelTitle.style.display = "none";
    return;
  }
  if (cnt) cnt.textContent = `n=${items.length}`;
  // 按日期降序:最近在前,未来的在尾部;今天之后的事件按升序归并展示会更直观
  const past = items.filter(i => i.event_date < today).sort((a, b) => dateCmp(b.event_date, a.event_date));
  const future = items.filter(i => i.event_date >= today).sort((a, b) => dateCmp(a.event_date, b.event_date));
  const row = e => `<div class="event-row${e.event_date >= today ? " ev-future" : ""}">
    <span class="event-date">${e.event_date.slice(5)}</span>
    <span>${e.name}</span>
    <span class="hint">${e.description || ""}</span></div>`;
  let html = "";
  if (future.length) html += `<p class="hint">—— 未来 ——</p>` + future.map(row).join("");
  if (past.length) html += `<p class="hint">—— 已发生(最近 30 天) ——</p>` + past.map(row).join("");
  box.innerHTML = html;
}

async function loadBrief() {
  const d = await fetch("/api/events?history=30&future=7").then(r => r.json()).catch(() => null);
  if (!d || !d.ok) {
    for (const c of CATS) {
      $("#ev-" + c.id).closest(".panel").style.display = "none";
    }
    $("#brief-toolbar").style.display = "none";
    return;
  }
  // need_backfill:库存无历史事件 → 显示回填按钮(一次性,幂等)
  if (d.need_backfill) {
    $("#brief-toolbar").style.display = "block";
  }
  const byCat = {};
  for (const c of CATS) byCat[c.id] = [];
  for (const e of d.items || []) {
    if (byCat[e.category] != null) byCat[e.category].push(e);
  }
  for (const c of CATS) {
    try { renderCategory(c.id, byCat[c.id], c.emptyNote); } catch (err) {
      console.error("分类渲染失败:", c.id, err);
    }
  }
}

async function backfillEvents() {
  const st = $("#brief-backfill-status");
  st.textContent = "回填中(Nasdaq 财报 30 天 × 逐日 + IPO × 按月…约 15-30 秒)";
  try {
    const d = await fetch("/api/events/backfill?days_back=30", { method: "POST" })
      .then(r => r.json());
    st.textContent = d.ok ? `新增 ${d.backfilled} 条` : "❌ " + (d.error || "失败");
  } catch (e) { st.textContent = "❌ " + e.message; }
  loadBrief();
}

// ---- 当日策略提醒(ma200_panic 双形态;无数据=整面板隐藏,不占位) ----
async function loadMaSignal() {
  const panel = $("#ma-reminder-panel");
  try {
    const d = await fetch("/api/backtest/ma_signal").then(r => r.json());
    if (!d || !d.ok) {
      panel.querySelector(".hint").textContent = "❌ " + (d && d.error || "策略提醒数据不可用");
      return;
    }
    const chg = d.px_chg_pct;
    const chgTxt = (chg == null) ? "" :
      ` 昨日 ${d.as_of.slice(5)} TQQQ <span class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${chg}%</span> 收 $${fmtNum(d.px)}`;
    const asof = $("#ma-asof");
    asof.innerHTML = `数据截至 ${d.as_of}:${chgTxt}`
      + (d.stale_days > 4 ? ` · <span class="warn">⚠ 已 ${d.stale_days} 天未更新</span>` : "");
    const cls = { buy: "pos", sell: "neg", hold: "", wait: "warn" };
    const pos = d.px_ratio == null ? "" :
      `收盘 $${fmtNum(d.px)} · 200MA $${fmtNum(d.ma200)}(现价/200MA = ×${d.px_ratio}) · 500MA $${fmtNum(d.ma500)}`;
    $("#ma-reminder").innerHTML = (d.strategies || []).map(s => `
      <div class="ma-card">
        <div class="ma-name">TQQQ · ${escBrief(s.name)}</div>
        <div class="ma-action ${cls[s.action] || ""}">${escBrief(s.action_text)}</div>
        <div class="hint">${pos}${s.last_flip ? ` · 最近翻转: ${s.last_flip[0]} → ${s.last_flip[1]}` : ""}</div>
        ${(s.distances || []).map(x => `
          <div class="ma-dist">${escBrief(x.label)}:
            <b class="${x.pct >= 0 ? "pos" : "neg"}">${x.pct >= 0 ? "+" : ""}${x.pct}%</b> → $${fmtNum(x.target)}
            ${x.note ? `<span class="hint">(${escBrief(x.note)})</span>` : ""}</div>`).join("")}
      </div>`).join("");
  } catch (e) {
    console.error("策略提醒加载失败:", e);
    $("#ma-reminder").innerHTML =
      "<p class='hint warn'>❌ 策略提醒加载失败(本地数据/服务不可用),不显示替代数据。</p>";
  }
}

loadBrief();
loadMaSignal();
