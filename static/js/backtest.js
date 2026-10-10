/* 回测工作台：策略表单动态渲染 + ECharts 结果可视化 */
"use strict";

const $ = (sel) => document.querySelector(sel);
let catalog = [];

// ---------- 策略下拉与动态表单 ----------
async function loadStrategies() {
  const res = await fetch("/api/backtest/strategies");
  catalog = await res.json();
  const sel = $("#strategy-select");
  sel.innerHTML = catalog.map(s =>
    `<option value="${s.id}">${s.name}</option>`).join("");
  sel.addEventListener("change", renderParamForm);
  renderParamForm();
}

// 主标的：单标的语义（多填只取第一个；策略辅助资产由后端自动注入）
const singleTicker = () => {
  const parts = $("#tickers").value.split(",").map(s => s.trim().toUpperCase()).filter(Boolean);
  if (parts.length > 1) {
    document.getElementById("ticker-hint").textContent =
      `⚠ 已按单主标的运行：${parts[0]}（忽略了 ${parts.slice(1).join(", ")}）`;
  }
  return parts[0] || "";
};

// 默认日期 = 本地数据全范围（起始/结束的默认值，直接可跑完整窗口）
async function loadDefaultRange() {
  const t = singleTicker();
  if (!t) return;
  try {
    const res = await fetch(`/api/backtest/range?tickers=${encodeURIComponent(t)}`);
    if (!res.ok) return;
    const r = await res.json();
    $("#start").value = r.start;
    $("#end").value = r.end;
    const per = Object.entries(r.per_ticker || {})
      .map(([k, v]) => `${k}: ${v.start} → ${v.end}`).join(" · ");
    document.getElementById("range-hint").textContent =
      `默认已回填 ${t} 本地数据全范围：${r.start} → ${r.end}  [${per}]`;
  } catch (e) { /* 留空 = 引擎自动用全范围 */ }
}

function currentStrategy() {
  return catalog.find(s => s.id === $("#strategy-select").value);
}

function renderParamForm() {
  const s = currentStrategy();
  if (!s) return;
  $("#strategy-desc").textContent = s.description;
  $("#param-form").innerHTML = s.params.map(p => {
    const val = p.default ?? "";
    switch (p.type) {
      case "select":
        return `<label class="field"><span>${p.label}</span>
          <select data-param="${p.key}">${p.options.map(o =>
            `<option value="${o.value}" ${o.value === p.default ? "selected" : ""}>${o.label}</option>`).join("")}
          </select></label>`;
      case "tickers":
        return `<label class="field"><span>${p.label}</span>
          <input type="text" data-param="${p.key}" value="${val}" spellcheck="false"></label>`;
      default:  // number / string
        return `<label class="field"><span>${p.label}</span>
          <input type="${p.type === "number" ? "number" : "text"}" data-param="${p.key}"
                 value="${val}" ${p.min !== undefined ? `min="${p.min}"` : ""}
                 ${p.max !== undefined ? `max="${p.max}"` : ""}
                 ${p.step !== undefined ? `step="${p.step}"` : ""}></label>`;
    }
  }).join("");
}

function collectParams() {
  const params = {};
  document.querySelectorAll("[data-param]").forEach(el => {
    let v = el.value;
    if (el.type === "number") v = v === "" ? null : Number(v);
    if (typeof v === "string" && v.trim() === "") v = null;
    if (v !== null) params[el.dataset.param] = v;
  });
  return params;
}

// ---------- 运行 ----------
$("#run-btn").addEventListener("click", async () => {
  const btn = $("#run-btn");
  const status = $("#run-status");
  btn.disabled = true;
  status.textContent = "回测中…（首次运行可能需要下载数据）";
  try {
    const t = singleTicker();
    if (!t) { status.textContent = "❌ 请填写主标的（如 TQQQ）"; return; }
    const body = {
      strategy: $("#strategy-select").value,
      tickers: [t],
      params: collectParams(),
      start: $("#start").value || null,
      end: $("#end").value || null,
      benchmarks: $("#benchmarks").value.split(",").map(s => s.trim().toUpperCase()).filter(Boolean),
      initial_capital: Number($("#capital").value) || 10000,
      rebalance_cost_bps: Number($("#cost").value) || 0,
      use_synth_tqqq: document.querySelector('input[name="tqqq-data"]:checked').value,
      execution_price: $("#execution").value,
    };
    const res = await fetch("/api/backtest/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    renderResult(data);
    status.textContent = `完成：${data.start} → ${data.end}`;
  } catch (e) {
    status.textContent = "❌ " + e.message;
  } finally {
    btn.disabled = false;
  }
});

// ---------- 渲染 ----------
const fmtPct = (v, d = 1) => (v * 100).toFixed(d) + "%";
const fmtNum = (v, d = 2) => Number(v).toFixed(d);
const fmtMoney = (v) => "$" + Math.round(v).toLocaleString("en-US");
// 总收益的直观口径：>=10 倍时用倍数（如 76.4x），否则百分比
const fmtTotal = (tr) => tr >= 9 ? (1 + tr).toFixed(1) + "x" : fmtPct(tr, 1);

function perfCards(p, opts = {}) {
  const nm = opts.name ? `${opts.name} ` : "";   // 卡片标注主标的(如 "TQQQ 年化 IRR")
  // 定投等现金流场景:主口径用真钱权 XIRR;等效一次性(cagr)放在 BARRA sub 提示
  const hasCF = p.invested != null && p.xirr != null;
  const irrVal = hasCF ? p.xirr : (p.cagr ?? 0);
  const irrLabel = hasCF
    ? `${nm}年化 IRR (XIRR·钱权)<span class="tip" title="XIRR=逐笔现金流的内部收益率,与外部网站定投场景的 IRR / Money-Weighted Return 同口径">↗</span>`
    : `${nm}年化 IRR (CAGR)<span class="tip" title="无外部现金流时 IRR≡CAGR=(期末/期初)^(1/年数)-1">↗</span>`;
  const irrSub = hasCF
    ? `等效一次性年化 ${fmtPct(p.cagr ?? 0, 2)} · 总收益 ${fmtTotal(p.total_return ?? 0)} / ${(p.years ?? 0).toFixed(1)} 年`
    : `总收益 ${fmtTotal(p.total_return ?? 0)} / ${(p.years ?? 0).toFixed(1)} 年`;
  const cards = [
    { label: irrLabel, value: fmtPct(irrVal, 2), cls: irrVal >= 0 ? "pos" : "neg", sub: irrSub },
  ];
  // 期末账户总额(用户主诉求:直观看到最后有多少钱;定投场景分母=总投入)
  if (opts.finalValue != null && opts.initialValue > 0) {
    cards.push({ label: `${nm}期末账户总额`, value: fmtMoney(opts.finalValue),
      cls: (opts.finalValue ?? 0) >= opts.initialValue ? "pos" : "neg",
      sub: hasCF ? `总投入 ${fmtMoney(opts.initialValue)} · ${(opts.finalValue / opts.initialValue).toFixed(1)}x`
                 : `初始 ${fmtMoney(opts.initialValue)} · ${(opts.finalValue / opts.initialValue).toFixed(1)}x` });
  }
  cards.push(
    { label: `${nm}最大回撤`, value: fmtPct(p.max_drawdown ?? 0, 1), cls: "neg",
      sub: `Calmar ${fmtNum(p.calmar ?? 0)}` },
    { label: "Sharpe", value: p.sharpe != null ? fmtNum(p.sharpe) : "—", cls: "",
      sub: `Sortino ${p.sortino != null ? fmtNum(p.sortino) : "—"}` },
    { label: `${nm}年化波动`, value: fmtPct(p.ann_vol ?? 0, 1), cls: "",
      sub: `胜率(日) ${fmtPct(p.win_rate_daily ?? 0, 0)} · (月) ${fmtPct(p.win_rate_monthly ?? 0, 0)}` },
    { label: `${nm}最好/最差年度`, value: `${fmtPct(p.best_year ?? 0, 0)} / ${fmtPct(p.worst_year ?? 0, 0)}`, cls: "",
      sub: `最长恢复 ${p.longest_recovery_days != null ? p.longest_recovery_days + " 日" : "—"}` },
  );
  return cards;
}

const cardsHtml = (cards) => cards.map(c =>
  `<div class="stat-card"><div class="label">${c.label}</div>
   <div class="value ${c.cls}">${c.value}</div><div class="sub">${c.sub}</div></div>`).join("");

function renderResult(data) {
  // 合成数据声明
  const notes = $("#notes");
  if (data.notes && data.notes.length) {
    notes.style.display = "block";
    notes.innerHTML = data.notes.map(n => `<div>${n}</div>`).join("");
  } else notes.style.display = "none";

  const p = data.perf || {};
  const navVals = (data.nav || {}).values || [];
  const capitalInput = Number($("#capital").value) || 10000;
  const initialValue = p.invested ?? (navVals.length ? navVals[0] : capitalInput);
  // 定投场景分母=总投入(期初+全部定投);纯策略=首日净值(=初始资金)
  const mainName = (data.tickers && data.tickers[0]) || "策略";      // 卡片标注主标的
  $("#perf-cards").innerHTML = cardsHtml(perfCards(p, {
    name: mainName,
    finalValue: navVals.length ? navVals[navVals.length - 1] : null,
    initialValue,
  }));

  // 基准对照卡（QQQ 等 benchmark 的完整指标，与策略同口径并排）
  const benchRow = $("#bench-cards");
  const benches = Object.entries(data.benchmarks || {});
  document.getElementById("bench-title").style.display = benches.length ? "block" : "none";
  benchRow.innerHTML = benches.map(([name, b]) => {
    const bp = b.perf || {};
    const bv = (b.nav || {}).values || [];
    const bHasCF = bp.invested != null && bp.xirr != null;
    const bInit = bp.invested ?? (bv.length ? bv[0] : capitalInput);
    const irrVal = bHasCF ? bp.xirr : (bp.cagr ?? 0);
    const irrLabel = bHasCF
      ? `${name} 年化 IRR (XIRR·钱权)`
      : `${name} 年化 IRR (CAGR)`;
    const irrSub = bHasCF
      ? `等效一次性 ${fmtPct(bp.cagr ?? 0, 2)} · 总收益 ${fmtTotal(bp.total_return ?? 0)} / ${(bp.years ?? 0).toFixed(1)} 年 · 镜像定投`
      : `总收益 ${fmtTotal(bp.total_return ?? 0)} / ${(bp.years ?? 0).toFixed(1)} 年 · 回测窗 ${data.start} → ${data.end}`;
    const denomTxt = bHasCF ? `总投入 ${fmtMoney(bInit)}` : `${fmtMoney(bInit)}`;
    const cards = [
      { label: irrLabel, value: fmtPct(irrVal, 2), cls: irrVal >= 0 ? "pos" : "neg", sub: irrSub },
      {
        label: `${name} 期末账户总额`,
        value: bv.length ? fmtMoney(bv[bv.length - 1]) : "—",
        cls: (bv.length && bv[bv.length - 1] >= bInit) ? "pos" : "neg",
        sub: bv.length ? `${denomTxt} · ${(bv[bv.length - 1] / bInit).toFixed(1)}x` : "",
      },
      { label: `${name} 最大回撤`, value: fmtPct(bp.max_drawdown ?? 0, 1), cls: "neg",
        sub: `Calmar ${fmtNum(bp.calmar ?? 0)}` },
    ];
    return cardsHtml(cards);
  }).join("");

  renderNavChart(data);
  renderDDChart(data);
  renderAnnualTable(p, data);
  renderDDTable(p);
}

let navChart = null, ddChart = null;

function renderNavChart(data) {
  navChart = navChart || echarts.init($("#nav-chart"));
  const series = [{
    name: Object.keys(data.benchmarks).length ? "策略" : "组合",
    type: "line", data: data.nav.values, showSymbol: false,
    lineStyle: { width: 2, color: "#58a6ff" },
  }];
  for (const [name, b] of Object.entries(data.benchmarks)) {
    series.push({
      name, type: "line", data: b.nav.values, showSymbol: false,
      lineStyle: { width: 1.5, type: "dashed", opacity: 0.75 },
    });
  }
  const dates = data.nav.dates;
  const marks = [];
  const ddList = (data.perf || {}).drawdown_top5 || [];
  if (ddList.length) {
    marks.push({
      markArea: {
        silent: true,
        itemStyle: { color: "rgba(248,81,73,0.08)" },
        data: ddList
          .filter(d => dates.includes(d.peak_date))
          .map(d => [{ xAxis: d.peak_date }, { xAxis: d.recovery_date || data.nav.dates[data.nav.dates.length - 1] }]),
      },
    });
    series[0] = Object.assign(series[0], marks[0]);
  }
  navChart.setOption({
    animation: false,
    grid: { left: 70, right: 20, top: 30, bottom: 60 },
    legend: { top: 0, textStyle: { color: "#8b949e" } },
    tooltip: { trigger: "axis", backgroundColor: "#1c2128", borderColor: "#2d333b",
               textStyle: { color: "#e6edf3" } },
    xAxis: { type: "category", data: dates, axisLabel: { color: "#8b949e" } },
    yAxis: {
      type: "log", axisLabel: { color: "#8b949e",
        formatter: v => v >= 1 ? v.toFixed(0) : v.toFixed(2) },
      splitLine: { lineStyle: { color: "#21262d" } },
    },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 8 }],
    series,
  }, { notMerge: true });
}

function renderDDChart(data) {
  // 前端由 nav 派生水下曲线
  const vals = data.nav.values;
  let peak = -Infinity;
  const dd = vals.map(v => {
    peak = Math.max(peak, v);
    return +(v / peak - 1).toFixed(6);
  });
  ddChart = ddChart || echarts.init($("#dd-chart"));
  ddChart.setOption({
    animation: false,
    grid: { left: 70, right: 20, top: 10, bottom: 40 },
    tooltip: { trigger: "axis", backgroundColor: "#1c2128", borderColor: "#2d333b",
               textStyle: { color: "#e6edf3" },
               valueFormatter: v => (v * 100).toFixed(1) + "%" },
    xAxis: { type: "category", data: data.nav.dates, axisLabel: { color: "#8b949e" } },
    yAxis: { axisLabel: { color: "#8b949e", formatter: v => v * 100 + "%" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series: [{ type: "line", data: dd, showSymbol: false, areaStyle: { opacity: 0.35 },
               lineStyle: { color: "#f85149" }, itemStyle: { color: "#f85149" } }],
  }, { notMerge: true });
}

function renderAnnualTable(p, data) {
  const ar = p.annual_returns || {};
  const colNames = ["策略", ...Object.keys(data.benchmarks || {})];
  const years = [...new Set([
    ...Object.keys(ar),
    ...Object.values(data.benchmarks || {}).flatMap(b => Object.keys(b.perf.annual_returns || {})),
  ])].sort();
  let html = "<table><thead><tr><th>年份</th>" + colNames.map((c, i) => `<th>${i === 0 ? "策略" : c}</th>`).join("") + "</tr></thead><tbody>";
  for (const y of years) {
    const cells = colNames.map((c, i) => {
      const m = i === 0 ? ar[y] : ((data.benchmarks[c] || {}).perf.annual_returns || {})[y];
      const v = m != null ? fmtPct(m, 1) : "—";
      return `<td class="${m != null && m < 0 ? "neg" : "pos"}">${v}</td>`;
    });
    html += `<tr><td>${y}</td>${cells.join("")}</tr>`;
  }
  $("#annual-table").innerHTML = html + "</tbody></table>";
}

function renderDDTable(p) {
  const dd = p.drawdown_top5 || [];
  if (!dd.length) { $("#dd-table").innerHTML = "<p class='hint'>无回撤记录</p>"; return; }
  let html = `<table><thead><tr><th>#</th><th>峰值日</th><th>谷底日</th><th>恢复日</th>
              <th>深度</th><th>水下日数</th><th>恢复用时</th></tr></thead><tbody>`;
  dd.forEach((d, i) => {
    html += `<tr><td>${i + 1}</td><td>${d.peak_date}</td><td>${d.trough_date}</td>
             <td>${d.recovery_date || "未恢复"}</td>
             <td class="neg">${fmtPct(d.depth, 1)}</td><td>${d.length_days}</td>
             <td>${d.recovery_days != null ? d.recovery_days + " 日" : "—"}</td></tr>`;
  });
  $("#dd-table").innerHTML = html + "</tbody></table>";
}

loadStrategies();
loadDefaultRange();
window.addEventListener("resize", () => { navChart && navChart.resize(); ddChart && ddChart.resize(); });

// ---------- 网格扫描（Phase 4）----------
$("#scan-btn").addEventListener("click", async () => {
  const s = currentStrategy();
  const st = $("#scan-status");
  const grid = {};
  for (const p of (s ? s.params : [])) {
    if (Array.isArray(p.grid) && p.grid.length > 1) grid[p.key] = p.grid;
  }
  if (!Object.keys(grid).length) {
    st.textContent = "当前策略无扫描参数（spec 未定义 grid）";
    return;
  }
  $("#scan-desc").textContent = "扫描维度: " + Object.keys(grid).join(", ");
  const btn = $("#scan-btn");
  btn.disabled = true;
  st.textContent = "扫描中…（组合数在响应中）";
  try {
    const body = {
      strategy: s.id,
      tickers: [singleTicker()].filter(Boolean),
      grid, start: $("#start").value || null, end: $("#end").value || null,
      use_synth_tqqq: document.querySelector('input[name="tqqq-data"]:checked').value,
    };
    const res = await fetch("/api/backtest/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.detail || "扫描失败");
    let html = `<table><thead><tr>${Object.keys(grid).map(k => `<th>${k}</th>`).join("")}
                <th>CAGR</th><th>MDD</th><th>Sharpe</th><th>Calmar</th></tr></thead><tbody>`;
    for (const r of d.rows.slice(0, 20)) {
      html += `<tr>${Object.keys(grid).map(k => `<td>${r.params[k]}</td>`).join("")}
        <td class="pos">${(r.cagr * 100).toFixed(1)}%</td>
        <td class="neg">${(r.mdd * 100).toFixed(1)}%</td>
        <td>${r.sharpe != null ? r.sharpe.toFixed(2) : "—"}</td>
        <td>${r.calmar != null ? r.calmar.toFixed(2) : "—"}</td></tr>`;
    }
    $("#scan-result").innerHTML = html + `</tbody></table>`;
    st.textContent = `共 ${d.n} 组合 · 表格为 Sharpe Top${Math.min(d.rows.length, 20)}`;
  } catch (e) {
    st.textContent = "❌ " + e.message;
  } finally {
    btn.disabled = false;
  }
});
