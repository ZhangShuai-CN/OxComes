/* /rates 宏观页(2026-10-11 从国债页改版):美债三档卡(2Y/10Y/30Y)+ 2s10s 利差
   + 10Y 走势 + 原油/裂解价差/美元指数/汇率(双向)/黄金/BTC 走势。
   无假数据铁律:某品种数据缺失则该面板隐藏;全缺时面板隐藏,不显示占位。 */
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
let yieldsChart = null;
const macroCharts = new Map();   // key → echarts 实例
// 多面板轮换 accent(深色 CVD 已校验的四色,与策略提醒 K 线图同源)
const PANEL_COLORS = ["#58a6ff", "#c98500", "#22a2ad", "#d55181"];

function card(label, value, sub, cls = "") {
  return `<div class="stat-card"><div class="label">${label}</div>
          <div class="value ${cls}">${value}</div><div class="sub">${sub || ""}</div></div>`;
}

const fmtNum = (v) => v >= 1000 ? v.toFixed(0)
             : v >= 100 ? v.toFixed(1)
             : v >= 1 ? v.toFixed(2)
             : v.toFixed(4);   // <1 = 倒数汇率(如日元/美元 0.0063),保留 4 位

async function loadRates() {
  const y = await fetch("/api/macro/yields?history=500").then(r => r.json()).catch(() => null);
  if (!y || !y.ok || !y.cards) return;

  const cards = [];
  const defs = [["2Y", "2Y 美债"], ["10Y", "10Y 美债"], ["30Y", "30Y 美债"]];
  for (const [key, label] of defs) {
    const c = y.cards[key];
    if (!c || c.value == null) continue;             // 数据缺失 → 不渲染该卡
    const chg = c.chg != null ? `日内 ${c.chg >= 0 ? "+" : ""}${c.chg}` : "";
    const src = c.source ? ` · ${c.source}` : "";
    const asOf = c.as_of ? ` · ${c.as_of}` : "";
    cards.push(card(label, c.value.toFixed(2) + "%",
                    chg + asOf + src, c.chg > 0 ? "neg" : (c.chg < 0 ? "pos" : "")));
  }
  if (y.spread_10Y_2Y != null) {
    // 倒挂(2s10s < 0)= 经典衰退预警信号,数值标红警示
    cards.push(card("10Y−2Y 利差", (y.spread_10Y_2Y > 0 ? "+" : "") + y.spread_10Y_2Y.toFixed(2),
                    y.spread_10Y_2Y < 0 ? "倒挂(衰退信号)" : "正常",
                    y.spread_10Y_2Y < 0 ? "neg" : ""));
  }
  if (!cards.length) {
    $("#rates-panel").style.display = "none";
  } else {
    $("#rates-cards").innerHTML = cards.join("");
    const asOf = Object.values(y.cards).map(c => c.as_of).filter(Boolean).sort().pop();
    $("#rates-meta").textContent = asOf ? `截至 ${asOf} · 本地行情` : "";
  }

  // 10Y 历史曲线:缺失时隐藏整面板
  if (y.series && y.series["10Y"] && y.series["10Y"].dates && y.series["10Y"].dates.length) {
    const s = y.series["10Y"];
    yieldsChart = yieldsChart || echarts.init($("#yields-chart"));
    yieldsChart.setOption({
      animation: false,
      grid: { left: 55, right: 15, top: 15, bottom: 28 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: s.dates,
               axisLabel: { color: "#8b949e" } },
      yAxis: { type: "value", scale: true, axisLabel: { color: "#8b949e", formatter: v => v + "%" },
               splitLine: { lineStyle: { color: "#21262d" } } },
      series: [{ type: "line", data: s.values, showSymbol: false,
                 lineStyle: { color: "#58a6ff" }, areaStyle: { opacity: 0.12 } }],
    }, { notMerge: true });
    const first = s.dates[0] || "";
    $("#rates-note-10y").textContent = `近 ${(y.history || 500)} 个交易日 · ${first} 起`;
  } else {
    $("#yields-chart").closest(".panel").style.display = "none";
  }
}

async function loadPanels() {
  const d = await fetch("/api/macro/panels?days=1000").then(r => r.json()).catch(() => null);
  if (!d || !d.ok || !d.charts) return;
  const order = ["cl", "crack", "dxy", "gold", "usdjpy", "jpyusd", "usdcny", "cnyusd", "btc"];
  order.forEach((key, i) => {
    const c = d.charts[key];
    const panel = $(`#panel-${key}`);
    if (!c || !c.dates || c.dates.length < 2) {
      if (panel) panel.style.display = "none";
      return;                                    // 无数据 → 隐藏面板(不占位)
    }
    const el = $(`#mchart-${key}`);
    if (!el) return;
    const color = PANEL_COLORS[i % PANEL_COLORS.length];
    const unit = c.unit || "";
    let ch = macroCharts.get(key);
    if (!ch) { ch = echarts.init(el); macroCharts.set(key, ch); }
    const fmt = v => unit === "$" ? "$" + fmtNum(v)
                 : unit ? fmtNum(v) + " " + unit
                 : fmtNum(v);
    ch.setOption({
      animation: false,
      grid: { left: 58, right: 15, top: 14, bottom: 26 },
      tooltip: { trigger: "axis",
                 valueFormatter: v => (v == null ? "—" : fmt(v)) },
      xAxis: { type: "category", data: c.dates, axisLabel: { color: "#8b949e" } },
      yAxis: { type: "value", scale: true,
               axisLabel: { color: "#8b949e", formatter: v => fmtNum(v) },
               splitLine: { lineStyle: { color: "#21262d" } } },
      series: [{ type: "line", data: c.values, showSymbol: false,
                 lineStyle: { color }, areaStyle: { opacity: 0.10 },
                 markLine: { symbol: "none", silent: true,   // 现价水平参考 + 线端值
                   lineStyle: { color: "#8b949e", type: "dotted", width: 1 },
                   label: { position: "end", color: "#e6edf3", fontSize: 11, fontWeight: "bold",
                            formatter: fmt(c.last) },
                   data: [{ yAxis: c.last }] } }],
    }, { notMerge: true });
    const noteEl = $(`#mnote-${key}`);
    if (noteEl) {
      noteEl.textContent = `现 ${fmt(c.last)} · 截至 ${c.as_of}`
        + (c.note ? " · " + c.note : "");
    }
  });
}

loadRates();
loadPanels();
window.addEventListener("resize", () => {
  yieldsChart && yieldsChart.resize();
  macroCharts.forEach(ch => ch.resize());
});
