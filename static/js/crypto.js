/* /crypto 加密总览:币种涨跌卡 + F&G 仪表/走势。
   F&G×BTC 回测已拆至 /crypto/backtest 页(2026-10-10)。
   无假数据铁律:F&G 缺失时不渲染仪表(不再显示 50),走势数据缺失时隐藏图块。 */
"use strict";
const $ = (s) => document.querySelector(s);
const fmtPct = (v, d = 1) => (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
let fngChart = null, gauge = null;

async function loadDaily() {
  const d = await fetch("/api/crypto/daily").then(r => r.json()).catch(() => null);
  if (!d || !d.ok || !d.items || !d.items.length) {
    $("#coin-cards").closest("div").style.display = "none";
    return;
  }
  const cards = d.items.map(c => {
    const cells = [["1d", c.changes["1d"]], ["7d", c.changes["7d"]], ["30d", c.changes["30d"]],
                   ["90d", c.changes["90d"]], ["1y", c.changes["365d"]]];
    const sub = cells.filter(x => x[1] != null)
      .map(([k, v]) => `${k} ${fmtPct(v, 0)}`).join(" · ");
    return `<div class="stat-card"><div class="label">${c.name} <span class="hint">${c.id}</span></div>
      <div class="value">${c.price >= 100 ? "$" + Math.round(c.price).toLocaleString()
        : "$" + c.price.toFixed(c.price >= 1 ? 2 : 4)}</div>
      <div class="sub">${sub}</div></div>`;
  }).join("");
  $("#coin-cards").innerHTML = cards;
  if (d.fng) $("#fng-now").textContent = `${d.fng.label} ${d.fng.value} (截至 ${d.fng.as_of})`;
  renderGauge(d.fng);
}

function renderGauge(fng) {
  if (!fng) { $("#fng-gauge").style.display = "none"; return; }   // 无数据不渲染,不显示 50
  gauge = gauge || echarts.init($("#fng-gauge"));
  gauge.setOption({
    animation: false,
    series: [{
      type: "gauge", min: 0, max: 100, splitNumber: 4,
      axisLine: { lineStyle: { width: 18,
        color: [[0.25, "#f85149"], [0.45, "#d29922"], [0.55, "#8b949e"], [0.75, "#58a6ff"], [1, "#3fb950"]] } },
      pointer: { itemStyle: { color: "#e6edf3" }, length: "60%", width: 5 },
      axisTick: { lineStyle: { color: "#8b949e" } },
      splitLine: { lineStyle: { color: "#8b949e", width: 2 } },
      axisLabel: { color: "#8b949e", distance: 24, fontSize: 11 },
      detail: { color: "#e6edf3", fontSize: 26, offsetCenter: [0, "62%"],
                formatter: () => fng.label },
      data: [{ value: fng.value }],
    }],
  }, { notMerge: true });
}

async function loadFng() {
  const d = await fetch("/api/crypto/fng?days=1095").then(r => r.json()).catch(() => null);
  if (!d || !d.ok) { $("#fng-chart").style.display = "none"; return; }
  const marks = [
    { yAxis: 25, label: "极度恐惧" }, { yAxis: 75, label: "极度贪婪" },
  ];
  fngChart = fngChart || echarts.init($("#fng-chart"));
  fngChart.setOption({
    animation: false,
    grid: { left: 40, right: 10, top: 12, bottom: 24 },
    tooltip: { trigger: "axis", backgroundColor: "#1c2128", borderColor: "#2d333b",
               textStyle: { color: "#e6edf3" } },
    xAxis: { type: "category", data: d.dates, axisLabel: { color: "#8b949e" } },
    yAxis: { type: "value", min: 0, max: 100, axisLabel: { color: "#8b949e" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series: [{ type: "line", data: d.values, showSymbol: false,
               lineStyle: { color: "#d29922" }, itemStyle: { color: "#d29922" },
               areaStyle: { opacity: 0.15 },
               markLine: { silent: true, symbol: "none",
                 data: marks.map(m => ({ yAxis: m.yAxis,
                   lineStyle: { color: "#58a6ff", type: "dashed", opacity: .5 },
                   label: { color: "#8b949e", formatter: m.label, position: "insideEndTop" } })) } }],
  }, { notMerge: true });
}

loadDaily();
loadFng();
window.addEventListener("resize", () => {
  gauge && gauge.resize(); fngChart && fngChart.resize();
});
