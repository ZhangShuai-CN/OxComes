/* 板块轮动页：当日涨跌条形 / 动量热力表 / 轮动象限 / RS 曲线 */
"use strict";

let snap = null;
let rsChart = null, quadChart = null, barChart = null;
const picked = new Set(["XLK"]);

function fmtPct(v) { return v == null ? "—" : v.toFixed(2) + "%"; }
function pct(v) { return v == null ? 0 : v; }

async function load() {
  const res = await fetch("/api/sectors/daily");
  snap = await res.json();
  if (!snap.ok) {
    document.body.insertAdjacentHTML("afterbegin",
      `<div class="synth-note">${snap.error || "数据不可用"}</div>`);
    return;
  }
  document.getElementById("as-of").textContent = `（截至 ${snap.as_of}）`;
  document.getElementById("sectors-note").textContent = snap.note || "";
  renderBar();
  renderMomentum();
  renderQuadrant();
  renderRsPicks();
  renderRS();
}

function renderBar() {
  const sorted = [...snap.sectors].sort((a, b) => b.change_pct - a.change_pct);
  barChart = barChart || echarts.init(document.getElementById("sectors-bar"));
  barChart.setOption({
    animation: false,
    grid: { left: 70, right: 30, top: 10, bottom: 30 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", axisLabel: { formatter: v => v + "%", color: "#8b949e" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    yAxis: { type: "category", data: sorted.map(s => s.name + " " + s.ticker),
             axisLabel: { color: "#e6edf3" } },
    series: [{
      type: "bar",
      data: sorted.map(s => ({
        value: s.change_pct,
        itemStyle: { color: s.change_pct >= 0 ? "#3fb950" : "#f85149" },
      })),
      label: { show: true, position: "right", color: "#8b949e",
               formatter: p => p.value.toFixed(2) + "%" },
    }],
  }, { notMerge: true });
}

function renderMomentum() {
  const labels = snap.momentum_labels.slice(1);   // 除 1d 外展示
  const rows = [...snap.sectors].sort((a, b) => pct(b["mom_1m"]) - pct(a["mom_1m"]));
  let html = `<table><thead><tr><th>板块</th>${labels.map(l => `<th>${l}</th>`).join("")}
              <th>1d</th></tr></thead><tbody>`;
  for (const s of rows) {
    const tds = labels.map(l => {
      const v = s["mom_" + l];
      const cls = v == null ? "" : (v >= 0 ? "pos" : "neg");
      const bg = v == null ? "" :
        `background:rgba(${v >= 0 ? "63,185,80" : "248,81,73"},${Math.min(Math.abs(v) / 30, 0.55)})`;
      return `<td class="${cls}" style="${bg}">${fmtPct(v)}</td>`;
    }).join("");
    html += `<tr><td><span style="color:${s.color}">●</span> ${s.name}</td>${tds}
            <td class="${s.change_pct >= 0 ? "pos" : "neg"}">${fmtPct(s.change_pct)}</td></tr>`;
  }
  html += `<tr><td><b>当日涨幅前三</b></td><td colspan="6">${
    [...snap.sectors].sort((a, b) => b.change_pct - a.change_pct).slice(0, 3)
      .map(s => `${s.name} +${s.change_pct.toFixed(2)}%`).join(" · ")}</td></tr></tbody>`;
  document.getElementById("momentum-table").innerHTML = html;
}

function renderQuadrant() {
  quadChart = quadChart || echarts.init(document.getElementById("quadrant-chart"));
  quadChart.setOption({
    animation: false,
    grid: { left: 50, right: 30, top: 20, bottom: 40 },
    tooltip: { formatter: p => `${p.data.name}: 1m ${p.data.x}%, 12m-1m ${p.data.y}%` },
    xAxis: { name: "1月动量%", axisLabel: { color: "#8b949e" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    yAxis: { name: "12月-1月动量差%", axisLabel: { color: "#8b949e" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series: [{
      type: "scatter", symbolSize: 22,
      data: snap.quadrant.map(q => ({
        name: q.name, value: [q.x, q.y], x: q.x, y: q.y,
        itemStyle: { color: (snap.sectors.find(s => s.ticker === q.ticker) || {}).color },
      })),
      label: { show: true, formatter: p => p.data.name, position: "top", color: "#8b949e" },
      markLine: { silent: true, lineStyle: { color: "#2d333b" },
                 data: [{ xAxis: 0 }, { yAxis: 0 }] },
    }],
  }, { notMerge: true });
}

function renderRsPicks() {
  const names = Object.keys(snap.rs_lines || {});
  document.getElementById("rs-picks").innerHTML = names.map(t => {
    const s = snap.sectors.find(x => x.ticker === t) || {};
    const on = picked.has(t) ? "checked" : "";
    return `<label class="rs-pick"><input type="checkbox" ${on} value="${t}">
            <span style="color:${s.color || "#999"}">●</span>${s.name || t}</label>`;
  }).join("");
  document.querySelectorAll("#rs-picks input").forEach(el =>
    el.addEventListener("change", () => {
      el.checked ? picked.add(el.value) : picked.delete(el.value);
      renderRS();
    }));
}

function renderRS() {
  rsChart = rsChart || echarts.init(document.getElementById("rs-chart"));
  const series = [];
  for (const t of picked) {
    const line = (snap.rs_lines || {})[t];
    if (!line) continue;
    const s = snap.sectors.find(x => x.ticker === t) || {};
    series.push({
      name: s.name || t, type: "line", data: line.values, showSymbol: false,
      lineStyle: { width: 1.8, color: s.color || "#58a6ff" },
    });
  }
  if (!series.length) { rsChart.clear(); return; }
  const first = snap.rs_lines[[...picked][0]];
  rsChart.setOption({
    animation: false,
    grid: { left: 60, right: 20, top: 30, bottom: 30 },
    legend: { top: 0, textStyle: { color: "#8b949e" } },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: first.dates, axisLabel: { color: "#8b949e" } },
    yAxis: { type: "value", scale: true, axisLabel: { color: "#8b949e" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series,
  }, { notMerge: true });
}

load();
window.addEventListener("resize", () => {
  barChart && barChart.resize(); quadChart && quadChart.resize(); rsChart && rsChart.resize();
});
