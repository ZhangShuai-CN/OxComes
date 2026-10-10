/* /rates 国债页:10Y/30Y/13W 利率卡 + 利差 + 10Y 历史曲线(2026-10-10 从每日消息拆出)。
   无假数据铁律:某档利率数据缺失则该卡不渲染;全缺时面板隐藏,不显示占位。 */
"use strict";
const $ = (s) => document.querySelector(s);
let yieldsChart = null;

function card(label, value, sub, cls = "") {
  return `<div class="stat-card"><div class="label">${label}</div>
          <div class="value ${cls}">${value}</div><div class="sub">${sub || ""}</div></div>`;
}

async function loadRates() {
  const y = await fetch("/api/macro/yields?history=500").then(r => r.json()).catch(() => null);
  if (!y || !y.ok || !y.cards) return;

  const cards = [];
  const defs = [["10Y", "10Y 美债", "pos"], ["30Y", "30Y 美债", "pos"], ["13W", "13W 国库券", "pos"]];
  for (const [key, label, dvCls] of defs) {
    const c = y.cards[key];
    if (!c || c.value == null) continue;             // 数据缺失 → 不渲染该卡
    const chg = c.chg != null ? `日内 ${c.chg >= 0 ? "+" : ""}${c.chg}` : "";
    const src = c.source ? ` · ${c.source}` : "";
    const asOf = c.as_of ? ` · ${c.as_of}` : "";
    cards.push(card(label, c.value.toFixed(2) + "%",
                    chg + asOf + src, c.chg > 0 ? "neg" : (c.chg < 0 ? dvCls : "")));
  }
  if (y.spread_10Y_13W != null) {
    cards.push(card("10Y−13W 利差", y.spread_10Y_13W.toFixed(2),
                    y.spread_10Y_13W < 0 ? "倒挂(衰退信号)" : "正常"));
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
  } else {
    $("#yields-chart").closest(".panel").style.display = "none";
  }
}

loadRates();
window.addEventListener("resize", () => yieldsChart && yieldsChart.resize());
