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

// ---- 当日策略提醒 K 线图:TQQQ 蜡烛 + 4 条策略线 + 买卖信号打点,线端标注现价距该线的百分比差距 ----
// 线色经暗色 CVD 校验(线间 all-pairs ΔE ≥ 12、亮度带内);蜡烛红/绿是涨跌语义色,
// 以「阳线空心 / 阴线实心」形态通道补足红绿色弱下的区分;打点绿▲=买、红▼=卖
// (同为涨跌语义色,形状方向双通道区分),白描边保证黑底可辨认;
// 打点系列默认隐藏,点 legend 按钮显示。
const maCharts = new Map();   // ticker → echarts 实例(多标的各一张 K 线图)
const maLineColor = { ma200: "#3d85e0", ma500: "#c98500", oh_sell: "#d55181", oh_rebuy: "#22a2ad" };

const maFlipColor = { buy: "#3fb950", sell: "#f85149" };

function renderMaChart(el, d) {
  const c = d.chart;
  if (!c || !c.dates || c.dates.length < 2) { el.style.display = "none"; return; }
  const tk = d.ticker || "TQQQ";
  let maChart = maCharts.get(tk);
  if (!maChart) { maChart = echarts.init(el); maCharts.set(tk, maChart); }
  const px = d.px;
  // 线端差距 ±%:与 distances 文本同口径 target/px-1(到达该线还需的价格变动,正 = 还需涨)
  const lastOf = (a) => { for (let i = a.length - 1; i >= 0; i--) if (a[i] != null) return a[i]; return null; };
  const pct = (v) => v == null ? "" :
    `${(v / px - 1) >= 0 ? "+" : ""}${((v / px - 1) * 100).toFixed(1)}%`;
  // 四线命名体系(2026-10-11 用户定名):趋势线/恐慌线/过热线/过热回调线。
  // 名称由后端 chart.line_labels 提供(锚均线/倍数参数变化时自动跟随),此处仅兜底
  const lineLabels = (c.line_labels || {});
  const lineDefs = [
    { key: "ma200", short: "趋势线", name: lineLabels.ma200 || "趋势线 MA200", dash: false },
    { key: "ma500", short: "恐慌线", name: lineLabels.ma500 || "恐慌线 MA500", dash: false },
    { key: "oh_sell", short: "过热线", name: lineLabels.oh_sell || "过热线 MA200×1.5", dash: true },
    { key: "oh_rebuy", short: "过热回调线", name: lineLabels.oh_rebuy || "过热回调线 MA200×1.2", dash: true },
  ];
  // 买卖信号打点:backend 只给窗口内翻转日(信号收盘确认日)。买点打 Low 下方、
  // 卖点打 High 上方(±2% 偏移避让蜡烛本体);c.kline 原序 [o, h, l, c]。
  const flipMap = {};
  for (const f of (c.flips || [])) flipMap[f.date] = f.type;
  const flipPts = { buy: [], sell: [] };
  c.dates.forEach((dt, i) => {
    const t = flipMap[dt], k = c.kline[i];
    if (!t || !k) return;
    flipPts[t].push([i, t === "buy" ? k[2] * 0.98 : k[1] * 1.02]);
  });
  // ECharts 蜡烛数据序是 [open, close, low, high];后端按 OHLC 落盘,这里转序
  const series = [{
    name: `${tk} 日K`, type: "candlestick", z: 3, barMaxWidth: 10,
    data: c.kline.map(k => [k[0], k[3], k[2], k[1]]),
    itemStyle: { color: "transparent", color0: "#f85149",
                 borderColor: "#3fb950", borderColor0: "#f85149", borderWidth: 1 },
    markLine: {   // 现价水平参考线:与各策略线的差距在图上直接目测
      symbol: "none", silent: true,
      lineStyle: { color: "#8b949e", type: "dotted", width: 1 },
      label: { position: "end", color: "#e6edf3", fontSize: 11, fontWeight: "bold",
               formatter: `现价 $${fmtNum(px)}` },
      data: [{ yAxis: px }],
    },
  }];
  for (const L of lineDefs) {
    series.push({
      name: L.name, type: "line", z: 4, data: c[L.key],
      showSymbol: false, connectNulls: false,
      lineStyle: { width: 2, color: maLineColor[L.key], type: L.dash ? "dashed" : "solid", opacity: 0.95 },
      endLabel: { show: true, distance: 6, color: "#8b949e", fontSize: 11,
                  formatter: `${L.short} ${pct(lastOf(c[L.key]))}` },
      labelLayout: { hideOverlap: true },
    });
  }
  for (const t of ["buy", "sell"]) {
    series.push({
      name: t === "buy" ? "买入信号日" : "卖出信号日", type: "scatter", z: 5,
      data: flipPts[t], symbol: "triangle", symbolSize: 14,
      symbolRotate: t === "sell" ? 180 : 0,
      // 白描边勾轮廓:黑底上绿/红填充色块不被背景吞掉(黑描边实测看不清色)
      itemStyle: { color: maFlipColor[t], borderColor: "#e6edf3", borderWidth: 1.5 },
    });
  }
  maChart.setOption({
    animation: false,
    backgroundColor: "transparent",
    grid: { left: 52, right: 138, top: 34, bottom: 26 },
    legend: { top: 0, right: 0, itemWidth: 16, itemHeight: 2, itemGap: 14,
              textStyle: { color: "#8b949e", fontSize: 11 }, inactiveColor: "#484f58",
              // 买卖打点默认隐藏(2026-10-11 用户拍板),点击 legend 按钮切换;
              // tooltip 的信号日提示行不受影响(hover 始终显示)
              selected: { "买入信号日": false, "卖出信号日": false } },
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross", lineStyle: { color: "#2d333b" } },
      backgroundColor: "#1c2128", borderColor: "#2d333b",
      textStyle: { color: "#e6edf3", fontSize: 12 },
      formatter(ps) {
        const i = ps[0].dataIndex, k = c.kline[i];
        if (!k) return "";
        const prev = i > 0 ? c.kline[i - 1][3] : null;
        const chg = prev ? `(<span class="${k[3] >= prev ? "pos" : "neg"}">${k[3] >= prev ? "+" : ""}${((k[3] / prev - 1) * 100).toFixed(2)}%</span>)` : "";
        let html = `<b>${c.dates[i]}</b> · ${tk} — C $${fmtNum(k[3])} ${chg}<br>` +
          `O $${fmtNum(k[0])} · H $${fmtNum(k[1])} · L $${fmtNum(k[2])}`;
        for (const L of lineDefs) {
          const v = c[L.key][i];
          html += `<br>${L.name}: ${v == null ? "—" : "$" + fmtNum(v)} ${pct(v)}`;
        }
        const ft = flipMap[c.dates[i]];
        if (ft) {
          html += `<br><b style="color:${maFlipColor[ft]}">${ft === "buy" ? "▲ 买入信号日" : "▼ 卖出信号日"}</b>` +
            "(当日收盘确认,次一交易日开盘成交)";
        }
        return html;
      },
    },
    xAxis: { type: "category", data: c.dates,
             axisLine: { lineStyle: { color: "#2d333b" } },
             axisLabel: { color: "#8b949e", fontSize: 11 } },
    yAxis: { type: "value", scale: true,
             axisLabel: { color: "#8b949e", fontSize: 11, formatter: v => "$" + fmtNum(v) },
             splitLine: { lineStyle: { color: "#21262d", width: 1 } } },
    dataZoom: [{ type: "inside" }],
    series,
  });
}

// ---- 当日策略提醒(多标的,每标的一块:标题+自己的K线图+自己的信号卡;单标的缺数据=该行披露) ----
async function loadMaSignal() {
  const panel = $("#ma-reminder-panel");
  const wrap = $("#ma-reminder");
  try {
    const d = await fetch("/api/backtest/ma_signal").then(r => r.json());
    if (!d || !d.ok) {
      wrap.innerHTML = "";
      panel.querySelector(".hint").textContent = "❌ " + (d && d.error || "策略提醒数据不可用");
      return;
    }
    const cls = { buy: "pos", sell: "neg", hold: "", wait: "warn" };
    wrap.innerHTML = "";
    (d.signals || []).forEach(sig => {
      if (!sig.ok) {   // 单标的缺数据:一行警告,不影响其他标的
        wrap.insertAdjacentHTML("beforeend",
          `<p class="hint warn">⚠ ${sig.ticker}: ${escBrief(sig.error)}</p>`);
        return;
      }
      const label = sig.chart.line_labels || {};
      const chg = sig.px_chg_pct;
      const chgTxt = (chg == null) ? "" :
        ` · 昨日 <span class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "+" : ""}${chg}%</span> 收 $${fmtNum(sig.px)}`
        + (sig.stale_days > 4 ? ` · <span class="warn">⚠ 已 ${sig.stale_days} 天未更新</span>` : "");
      const hist = (sig.strategies || []).map(s => s.last_flip ? `最近翻转: ${s.last_flip[0]} → ${s.last_flip[1]}` : "").filter(Boolean)[0] || "";
      // 块结构:标题 → 该标的的 K 线图 → 该标的的信号卡(同标的的图与卡上下紧邻)
      const block = document.createElement("div");
      block.className = "ma-block";
      block.innerHTML = `
        <h3 class="ma-title">${sig.ticker} · ${escBrief(sig.label || "")} <span class="hint">数据截至 ${sig.as_of}${chgTxt}</span></h3>
        <div class="ma-cards">
        ${(sig.strategies || []).map(s => `
          <div class="ma-card">
            <div class="ma-name">${sig.ticker} · ${escBrief(s.name)}</div>
            <div class="ma-action ${cls[s.action] || ""}">${escBrief(s.action_text)}</div>
            <div class="hint">收盘 $${fmtNum(sig.px)} · ${label.ma200 || "趋势线"} $${fmtNum(sig.ma200)}(现价/趋势线 = ×${sig.px_ratio})
              · ${label.ma500 || "恐慌线"} $${fmtNum(sig.ma500)}${hist ? ` · ${hist}` : ""}</div>
            ${(s.distances || []).map(x => `
              <div class="ma-dist">${escBrief(x.label)}:
                <b class="${x.pct >= 0 ? "pos" : "neg"}">${x.pct >= 0 ? "+" : ""}${x.pct}%</b> → $${fmtNum(x.target)}
                ${x.note ? `<span class="hint">(${escBrief(x.note)})</span>` : ""}</div>`).join("")}
          </div>`).join("")}
        </div>`;
      const chartEl = document.createElement("div");
      chartEl.style.height = "360px";
      chartEl.style.margin = "8px 0 2px";
      block.insertBefore(chartEl, block.querySelector(".ma-cards"));   // 图插在标题与卡之间
      wrap.appendChild(block);
      renderMaChart(chartEl, sig);
    });
    $("#ma-asof").textContent = "";   // 各块自带数据日期,头部不再重复
  } catch (e) {
    console.error("策略提醒加载失败:", e);
    wrap.innerHTML =
      "<p class='hint warn'>❌ 策略提醒加载失败(本地数据/服务不可用),不显示替代数据。</p>";
  }
}

window.addEventListener("resize", () => maCharts.forEach(ch => ch.resize()));

loadBrief();
loadMaSignal();
