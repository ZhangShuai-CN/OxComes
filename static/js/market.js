/* /market 美股总览:热力图 treemap + 市场宽度 + VIX(2026-10-10 重组)。
   明细表已按需求剔除;详见块级 tooltip(各周期涨跌/收盘价/数据日)。
   无假数据铁律:指标/图数据缺失即不渲染、面板隐藏;「最近交易日」视图下
   数据未同步至基准日的标的直接隐藏并在标题披露数量(不拿旧数据冒充今日)。 */
"use strict";
const $ = (s) => document.querySelector(s);
let chart = null, breadthChart = null, vixChart = null, data = null;
const periodNames = { "1d": "最近交易日", "1w": "1 周", "1m": "1 月", "3m": "3 月", "1y": "1 年" };

// ── 热力图 ──────────────────────────────────────────────────

// 色阶: -4%→深红, 0→中性, +4%→深绿(超界饱和)
function heatColor(v) {
  const t = Math.max(-1, Math.min(1, v / 0.04));
  if (t >= 0) {
    const a = 0.15 + 0.75 * Math.sqrt(t);
    return `rgba(63,185,80,${a.toFixed(3)})`;
  }
  const a = 0.15 + 0.75 * Math.sqrt(-t);
  return `rgba(248,81,73,${a.toFixed(3)})`;
}

function toTree(period) {
  // 缺所选周期数据的块(最近交易日视图下的未同步标的)过滤显示
  const avail = data.blocks.filter(b => b.changes[period] != null);
  const groups = {};
  for (const b of avail) {
    (groups[b.group] = groups[b.group] || []).push(b);
  }
  return Object.entries(groups).map(([g, items]) => ({
    name: g,
    itemStyle: { borderColor: "#21262d", borderWidth: 2 },
    children: items.map(b => ({
      name: `${b.name}\n${(b.changes[period] * 100 >= 0 ? "+" : "") + (b.changes[period] * 100).toFixed(1)}%`,
      value: b.weight,
      itemStyle: { color: heatColor(b.changes[period]), borderColor: "#0d1117", borderWidth: 1 },
      blockId: b.id,
      label: { color: "#e6edf3", fontSize: 13, overflow: "breakAll" },
    })),
  }));
}

function render() {
  const period = $("#hm-period").value || data.periods[0];
  chart = chart || echarts.init($("#heatmap"));
  chart.setOption({
    animation: false,
    tooltip: {
      backgroundColor: "#1c2128", borderColor: "#2d333b", textStyle: { color: "#e6edf3" },
      formatter: (info) => {
        const b = data.blocks.find(x => x.id === (info.data.blockId || ""));
        if (!b) return info.name;
        const lines = data.periods.map(k =>
          `${periodNames[k] || k}: ${b.changes[k] != null ? (b.changes[k] * 100).toFixed(2) + "%" : "数据未同步"}`);
        return `<b>${b.id}</b> ${b.name}<br>${lines.join("<br>")}<br>close ${b.close.toFixed(2)} (${b.last})`;
      },
    },
    series: [{
      type: "treemap", roam: false, nodeClick: false,
      breadcrumb: { show: false },
      top: 5, left: 0, right: 0, bottom: 5,
      label: { show: true, formatter: "{b}" },
      upperLabel: { show: true, height: 22, color: "#8b949e" },
      itemStyle: { gapWidth: 2 },
      data: toTree(period),
    }],
  }, { notMerge: true });
}

async function loadHeatmap() {
  const d = await fetch("/api/market/heatmap").then(r => r.json()).catch(() => null);
  if (!d || !d.ok) {
    $("#hm-meta").textContent = "❌ " + ((d && d.error) || "加载失败");
    return;
  }
  data = d;
  let meta = `基于本地行情 · 最近交易日 ${d.as_of} · ${d.blocks.length} 块`;
  if (d.stale && d.stale.length) {
    meta += ` · ${d.stale.length} 标的未同步至 ${d.as_of}(最近交易日视图下隐藏): ` +
            d.stale.map(s => `${s.id}(${s.last})`).join("、");
  }
  $("#hm-meta").textContent = meta;
  $("#hm-period").innerHTML = d.periods.map(p =>
    `<option value="${p}" ${p === "1d" ? "selected" : ""}>${periodNames[p] || p}</option>`).join("");
  $("#hm-period").onchange = render;
  render();
}

// ── 宏观指标:市场宽度 + VIX(从 brief.js 迁入) ────────────────

function card(label, value, sub, cls = "") {
  return `<div class="stat-card"><div class="label">${label}</div>
          <div class="value ${cls}">${value}</div><div class="sub">${sub || ""}</div></div>`;
}

async function loadMacroStats() {
  const [breadth, vix] = await Promise.all([
    fetch("/api/macro/breadth").then(r => r.json()).catch(() => null),
    fetch("/api/macro/vix").then(r => r.json()).catch(() => null),
  ]);

  const cards = [];
  if (breadth && breadth.ok) {
    const cls = breadth.state === "improving" ? "pos" :
                (breadth.state === "narrowing" ? "warn" : "");
    cards.push(card("市场宽度", breadth.pctile_52w + "%位",
                    `21日 ${breadth.chg_21d}% · ${breadth.state_desc}`, cls));
  }
  if (vix && vix.ok) {
    const r = vix.regime || {};
    cards.push(card("VIX " + (r.emoji || ""), vix.value,
                    `${r.label || ""} · ${vix.chg != null ? (vix.chg >= 0 ? "+" : "") + vix.chg : ""}`,
                    (r.level || 0) >= 3 ? "warn" : ""));
    $("#vix-meta").textContent = vix.series ? "近一年走势" :
      "仅当日快照(历史待 cli.py sync --tickers ^VIX)";
  }
  $("#mkt-cards").innerHTML = cards.join("");

  // 宽度曲线:数据缺失时隐藏面板(不画空图)
  if (breadth && breadth.ok && breadth.series) {
    breadthChart = breadthChart || echarts.init($("#breadth-chart"));
    breadthChart.setOption({
      animation: false,
      grid: { left: 55, right: 15, top: 10, bottom: 25 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: breadth.series.dates, axisLabel: { color: "#8b949e" } },
      yAxis: { type: "value", scale: true, axisLabel: { color: "#8b949e" },
               splitLine: { lineStyle: { color: "#21262d" } } },
      series: [{ type: "line", data: breadth.series.values, showSymbol: false, areaStyle: { opacity: 0.2 },
                 lineStyle: { color: "#d29922" }, itemStyle: { color: "#d29922" } }],
    }, { notMerge: true });
  } else {
    $("#breadth-chart").closest(".panel").style.display = "none";
  }

  // VIX 曲线:只有当日快照(无 series)时隐藏曲线 div,保留 stat 卡
  if (vix && vix.ok && vix.series) {
    vixChart = vixChart || echarts.init($("#vix-chart"));
    vixChart.setOption({
      animation: false,
      grid: { left: 55, right: 15, top: 10, bottom: 25 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: vix.series.dates, axisLabel: { color: "#8b949e" } },
      yAxis: { type: "value", scale: true, axisLabel: { color: "#8b949e" },
               splitLine: { lineStyle: { color: "#21262d" } } },
      series: [{ type: "line", data: vix.series.values, showSymbol: false, areaStyle: { opacity: 0.15 },
                 lineStyle: { color: "#bc8cff" }, itemStyle: { color: "#bc8cff" } }],
    }, { notMerge: true });
  } else {
    $("#vix-chart").style.display = "none";
    if (!(vix && vix.ok)) $("#vix-chart").closest(".panel").style.display = "none";
  }
}

loadHeatmap();
loadMacroStats();
window.addEventListener("resize", () => {
  chart && chart.resize();
  breadthChart && breadthChart.resize();
  vixChart && vixChart.resize();
});
