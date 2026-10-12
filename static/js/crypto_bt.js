/* /crypto/backtest 加密回测:F&G×BTC 择时回测(2026-10-10 从加密总览拆出独立页)。
   无假数据铁律:BTC 或 F&G 本地数据缺失时,后端返回 ok:False,页面只显示错误说明。 */
"use strict";
const $ = (s) => document.querySelector(s);
const fmtMoney = (v) => "$" + Math.round(v).toLocaleString("en-US");
let btChart = null;

async function runBt() {
  const st = $("#fng-bt-status");
  st.textContent = "回测中…";
  const q = `buy_below=${$("#fng-buy").value}&sell_above=${$("#fng-sell").value}&fee_bps=${$("#fng-fee").value}`;
  const d = await fetch(`/api/crypto/fng-btc?${q}`).then(r => r.json()).catch(() => null);
  if (!d || !d.ok) { st.textContent = "❌ " + ((d && d.error) || "运行失败"); return; }
  st.textContent = `窗口 ${d.window.start} → ${d.window.end}(${d.window.years.toFixed(1)} 年) · 触发调仓 ${d.n_trades} 次`;
  const s = d.strategy, b = d.buy_hold;
  const mult = v => (v / 10000).toFixed(1) + "x";
  $("#fng-bt-cards").innerHTML = [
    ["策略 期末(1万美元起)", fmtMoney(s.final_value_10k), `总收益 ${(mult(s.final_value_10k))} · IRR ${s.xirr == null ? (s.cagr*100).toFixed(2)+"%" : (s.xirr*100).toFixed(2)+"%"} · MDD ${(s.max_drawdown*100).toFixed(1)}%`],
    ["持有 BTC 期末", fmtMoney(b.final_value_10k), `总收益 ${mult(b.final_value_10k)} · CAGR ${(b.cagr*100).toFixed(2)}% · MDD ${(b.max_drawdown*100).toFixed(1)}%`],
  ].map(([l, v, sub]) =>
    `<div class="stat-card"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join("");
  btChart = btChart || echarts.init($("#fng-bt-chart"));
  btChart.setOption({
    animation: false,
    grid: { left: 55, right: 10, top: 14, bottom: 28 },
    legend: { top: 0, textStyle: { color: "#8b949e" } },
    tooltip: { trigger: "axis", backgroundColor: "#1c2128", borderColor: "#2d333b",
               textStyle: { color: "#e6edf3" } },
    xAxis: { type: "category", data: d.nav.dates, axisLabel: { color: "#8b949e" } },
    yAxis: { type: "log", axisLabel: { color: "#8b949e",
              formatter: v => "$" + v.toLocaleString() },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series: [
      { name: "F&G 择时", type: "line", data: d.nav.strategy, showSymbol: false,
        lineStyle: { width: 2, color: "#58a6ff" } },
      { name: "持有 BTC", type: "line", data: d.nav.buy_hold, showSymbol: false,
        lineStyle: { width: 1.5, type: "dashed", opacity: .8, color: "#d29922" } },
    ],
  }, { notMerge: true });
  $("#fng-bt-note").textContent =
    "F&G 自 2018-02 有数据(BTC 2017-08 起,回测自 2018-02);与美股回测同引擎(next_open 成交、防前视)。";
}

$("#fng-run").addEventListener("click", runBt);
runBt();

// ── AHR999/Mayer 乖离回测(2026-10-12):同引擎,参数化卖/买乖离阈值 ──
let ahrChart = null;

async function runAhrBt() {
  const st = document.getElementById("ahr-bt-status");
  st.textContent = "回测中…";
  const q = `sell_mult=${$("#ahr-sell").value}&buy_mult=${$("#ahr-buy").value}&fee_bps=${$("#ahr-fee").value}`;
  const d = await fetch(`/api/crypto/ahr-btc?${q}`).then(r => r.json()).catch(() => null);
  if (!d || !d.ok) { st.textContent = "❌ " + ((d && d.error) || "运行失败"); return; }
  st.textContent = `窗口 ${d.window.start} → ${d.window.end}(${d.window.years.toFixed(1)} 年) · 触发调仓 ${d.n_trades} 次 · 卖>x${d.params.sell_mult} / 买<x${d.params.buy_mult}`;
  const s = d.strategy, b = d.buy_hold;
  const mult = v => (v / 10000).toFixed(1) + "x";
  document.getElementById("ahr-bt-cards").innerHTML = [
    ["策略 期末(1万美元起)", fmtMoney(s.final_value_10k), `总收益 ${mult(s.final_value_10k)} · CAGR ${(s.cagr*100).toFixed(2)}% · MDD ${(s.max_drawdown*100).toFixed(1)}%`],
    ["持有 BTC 期末", fmtMoney(b.final_value_10k), `总收益 ${mult(b.final_value_10k)} · CAGR ${(b.cagr*100).toFixed(2)}% · MDD ${(b.max_drawdown*100).toFixed(1)}%`],
  ].map(([l, v, sub]) =>
    `<div class="stat-card"><div class="label">${l}</div><div class="value">${v}</div><div class="sub">${sub}</div></div>`).join("");
  ahrChart = ahrChart || echarts.init(document.getElementById("ahr-bt-chart"));
  ahrChart.setOption({
    animation: false,
    grid: { left: 55, right: 10, top: 14, bottom: 28 },
    legend: { top: 0, textStyle: { color: "#8b949e" } },
    tooltip: { trigger: "axis", backgroundColor: "#1c2128", borderColor: "#2d333b",
               textStyle: { color: "#e6edf3" } },
    xAxis: { type: "category", data: d.nav.dates, axisLabel: { color: "#8b949e" } },
    yAxis: { type: "log", axisLabel: { color: "#8b949e",
              formatter: v => "$" + v.toLocaleString() },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series: [
      { name: "AHR999 乖离择时", type: "line", data: d.nav.strategy, showSymbol: false,
        lineStyle: { width: 2, color: "#58a6ff" } },
      { name: "持有 BTC", type: "line", data: d.nav.buy_hold, showSymbol: false,
        lineStyle: { width: 1.5, type: "dashed", opacity: .8, color: "#d29922" } },
    ],
  }, { notMerge: true });
  document.getElementById("ahr-bt-note").textContent =
    `信号 t 日收盘(价格/MA200)→ t+1 开盘成交;费用双边 ${d.params.fee_bps}bps。指标现值与档位见「加密总览」信号灯。`;
}

document.getElementById("ahr-run").addEventListener("click", runAhrBt);
runAhrBt();
window.addEventListener("resize", () => { btChart && btChart.resize(); ahrChart && ahrChart.resize(); });
