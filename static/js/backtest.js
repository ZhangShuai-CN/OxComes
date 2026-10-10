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

// 各策略的默认基准对照：择时类策略需要跟「TQQQ 死拿」+「QQQ 标杆」双对照；
// 买入持有/定投本身就是 TQQQ 的持有形态，再对照 TQQQ 是重复 → 只留 QQQ
const DEFAULT_BENCHMARKS = {
  buy_hold: "QQQ",
  dca: "QQQ",
  ma200_panic: "TQQQ,QQQ",
  ma_cross: "TQQQ,QQQ",
};

function renderParamForm() {
  const s = currentStrategy();
  if (!s) return;
  $("#strategy-desc").textContent = s.description;
  $("#benchmarks").value = DEFAULT_BENCHMARKS[s.id] || "QQQ";
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
// ticker/系列名拼进 tooltip/表格 HTML 前的转义（防结构字符注入）
const esc = (s) => String(s).replace(/[<>&"]/g, c =>
  ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

function perfCards(p, opts = {}) {
  // 主策略卡统一「策略」前缀：对比基准卡用基准名（如 "TQQQ 年化 IRR"），
  // 避免主标的=TQQQ 且基准含 TQQQ 时两列卡片同名混淆
  // 定投等现金流场景:主口径用真钱权 XIRR;等效一次性(cagr)放在 sub 提示
  const hasCF = p.invested != null && p.xirr != null;
  const irrVal = hasCF ? p.xirr : (p.cagr ?? 0);
  const irrLabel = `策略年化 IRR`;
  const irrSub = hasCF
    ? `等效一次性年化 ${fmtPct(p.cagr ?? 0, 2)} · 总收益 ${fmtTotal(p.total_return ?? 0)} / ${(p.years ?? 0).toFixed(1)} 年`
    : `总收益 ${fmtTotal(p.total_return ?? 0)} / ${(p.years ?? 0).toFixed(1)} 年`;
  const cards = [
    { label: irrLabel, value: fmtPct(irrVal, 2), cls: irrVal >= 0 ? "pos" : "neg", sub: irrSub },
  ];
  // 期末账户总额(用户主诉求:直观看到最后有多少钱;定投场景分母=总投入)
  if (opts.finalValue != null && opts.initialValue > 0) {
    cards.push({ label: `策略期末账户总额`, value: fmtMoney(opts.finalValue),
      cls: (opts.finalValue ?? 0) >= opts.initialValue ? "pos" : "neg",
      sub: hasCF ? `总投入 ${fmtMoney(opts.initialValue)} · ${(opts.finalValue / opts.initialValue).toFixed(1)}x`
                 : `初始 ${fmtMoney(opts.initialValue)} · ${(opts.finalValue / opts.initialValue).toFixed(1)}x` });
  }
  cards.push(
    { label: `策略最大回撤`, value: fmtPct(p.max_drawdown ?? 0, 1), cls: "neg",
      sub: `Calmar ${fmtNum(p.calmar ?? 0)}` },
    { label: `策略总交易次数`, value: opts.nRebalanceDays != null ? String(opts.nRebalanceDays) : "—",
      cls: "", sub: `买 ${opts.nBuys ?? 0} 笔 · 卖 ${opts.nSells ?? 0} 笔`
        + (opts.nDeposits ? ` · 定注入金 ${opts.nDeposits} 期` : "") },
    { label: `策略年化波动`, value: fmtPct(p.ann_vol ?? 0, 1), cls: "",
      sub: `胜率(日) ${fmtPct(p.win_rate_daily ?? 0, 0)} · (月) ${fmtPct(p.win_rate_monthly ?? 0, 0)}` },
    { label: `策略最好/最差年度`, value: `${fmtPct(p.best_year ?? 0, 0)} / ${fmtPct(p.worst_year ?? 0, 0)}`, cls: "",
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
  const nDeposits = (data.trades || []).filter(t => t.side === "deposit").length;
  $("#perf-cards").innerHTML = cardsHtml(perfCards(p, {
    finalValue: navVals.length ? navVals[navVals.length - 1] : null,
    initialValue,
    nRebalanceDays: data.n_rebalance_days,
    nBuys: data.n_buys,
    nSells: data.n_sells,
    nDeposits,
  }));
  // 策略区两级标题：h3「策略」+ h4「{主标的} · {策略名}」（与基准分组 h4 同层级同字号，
  // 跑 TQQQ 时与「TQQQ · 买入持有」完全对仗）
  const sInfo = catalog.find(s => s.id === data.strategy_id);
  const sT = document.getElementById("strategy-title");
  const sSub = document.getElementById("strategy-subtitle");
  const mainTk = (data.tickers && data.tickers[0]) || "";
  sSub.textContent = `${mainTk} · ${sInfo ? sInfo.name : "策略"}`;
  sT.style.display = "block";
  sSub.style.display = "block";

  // 基准对照卡：每个基准一个分组标题 + 独占一行（Q 标的对照互不混排）
  const benchRow = $("#bench-cards");
  const benches = Object.entries(data.benchmarks || {});
  document.getElementById("bench-title").style.display = benches.length ? "block" : "none";
  benchRow.innerHTML = benches.map(([name, b]) => {
    const bp = b.perf || {};
    const bv = (b.nav || {}).values || [];
    const bHasCF = bp.invested != null && bp.xirr != null;
    const bInit = bp.invested ?? (bv.length ? bv[0] : capitalInput);
    const irrVal = bHasCF ? bp.xirr : (bp.cagr ?? 0);
    const irrLabel = `${name} 年化 IRR`;
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
    // 基准类型：普通=买入持有；主策略定投时基准镜像同额现金流→镜像定投
    const bType = bHasCF ? "镜像定投" : "买入持有";
    return `<div class="bench-group"><h4>${esc(name)} · ${bType}</h4>
            <div class="stat-row">${cardsHtml(cards)}</div></div>`;
  }).join("");

  renderNavChart(data);
  renderDDChart(data);
  renderAnnualChart(data);
  renderDDTable(p);
  renderTradesTable(data);
}

let navChart = null, ddChart = null, annualChart = null;

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

  // ---- 交易打点：按日聚合（▲净加仓 / ▼净减仓 / ◆同日双向换仓）+ 定投入金小点 ----
  // 打点落在 nav 曲线当日值上；y>0 过滤是对数轴的防御（nav 起点即资金，恒正）
  const navByDate = new Map(dates.map((d, i) => [d, data.nav.values[i]]));
  const tradesByDay = new Map();          // date -> 当日全部事件（含 deposit），tooltip 用
  for (const t of (data.trades || [])) {
    if (!navByDate.has(t.date)) continue; // 事件日不在 nav 曲线上（防御，正常不会发生）
    tradesByDay.set(t.date, (tradesByDay.get(t.date) || []).concat(t));
  }
  const buyPts = [], sellPts = [], bothPts = [], depPts = [];
  for (const [d, evs] of tradesByDay) {
    if (!(navByDate.get(d) > 0)) continue;
    let buy = 0, sell = 0, hasDeposit = false;
    evs.forEach(t => {
      if (t.side === "buy") buy += t.delta;
      else if (t.side === "sell") sell += -t.delta;
      else hasDeposit = true;
    });
    const pt = { value: [d, navByDate.get(d)] };
    if (hasDeposit) depPts.push(pt);
    if (buy > 0 && sell > 0) bothPts.push(pt);
    else if (buy > 0) buyPts.push(pt);
    else if (sell > 0) sellPts.push(pt);
    // 纯入金日（权重不变）只画入金点
  }
  const tradeSeries = (name, pts, symbol, color, opt = {}) => pts.length ? [{
    name, type: "scatter", data: pts, symbol,
    symbolSize: opt.size || 9, symbolRotate: opt.rotate || 0, z: 3,
    itemStyle: { color, borderColor: "#0d1117", borderWidth: 1 },
    emphasis: { scale: 1.4 },
  }] : [];
  series.push(
    ...tradeSeries("加仓", buyPts, "triangle", "#3fb950"),
    ...tradeSeries("减仓", sellPts, "triangle", "#f85149", { rotate: 180 }),
    ...tradeSeries("双向换仓", bothPts, "diamond", "#d29922"),
    ...tradeSeries("定投入金", depPts, "circle", "rgba(88,166,255,0.85)", { size: 6 }),
  );

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
               textStyle: { color: "#e6edf3" },
               // 当日成交明细并入 axis tooltip（散点系列的数值与曲线重复，不单列行）
               formatter: (params) => {
                 const ps = Array.isArray(params) ? params : [params];
                 const date = ps[0].axisValue;
                 let html = `<b>${date}</b>`;
                 ps.forEach(p => {
                   if (p.seriesType === "scatter") return;
                   const v = Array.isArray(p.value) ? p.value[1] : p.value;
                   if (v == null) return;
                   html += `<br>${p.marker} ${esc(p.seriesName)} ${fmtMoney(v)}`;
                 });
                 const evs = tradesByDay.get(date);
                 if (evs) html += evs.map(t =>
                   t.side === "deposit"
                     ? `<br><span style="color:#58a6ff">⊕ 定投入金 ${fmtMoney(t.amount ?? 0)}</span>`
                     : `<br>${t.side === "buy" ? "▲" : "▼"} ${esc(t.ticker)} 权重 ${fmtPct(t.delta, 1)} → ${fmtPct(t.weight_after, 0)}`
                       + (t.price != null ? ` @ $${t.price.toFixed(2)}` : "")
                 ).join("");
                 return html;
               } },
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

function renderAnnualChart(data) {
  const ar = (data.perf || {}).annual_returns || {};
  const benches = Object.entries(data.benchmarks || {});
  // 年份 = 策略与全部基准年度收益的并集（有数据的年份才出柱）
  const years = [...new Set([
    ...Object.keys(ar),
    ...benches.flatMap(([, b]) => Object.keys(b.perf.annual_returns || {})),
  ])].sort();
  annualChart = annualChart || echarts.init($("#annual-chart"));
  // 系列固定配色：策略=蓝（与净值图主曲线同色），基准轮转黄/紫/粉/青；
  // 正负由柱向表达（0 轴上下），颜色只负责区分系列
  const barSeries = (name, seriesAR) => ({
    name, type: "bar",
    data: years.map(y => seriesAR[y] ?? null),   // 该年无数据 → 空柱
    barMaxWidth: 34,
  });
  const series = [barSeries(
    Object.keys(data.benchmarks).length ? "策略" : "组合", ar)];
  for (const [name, b] of benches) {
    series.push(barSeries(name, b.perf.annual_returns || {}));
  }
  annualChart.setOption({
    animation: false,
    color: ["#58a6ff", "#d29922", "#a371f7", "#db61a2", "#39c5cf"],
    grid: { left: 56, right: 20, top: 30, bottom: 30 },
    legend: { top: 0, textStyle: { color: "#8b949e" } },
    tooltip: { trigger: "axis", backgroundColor: "#1c2128", borderColor: "#2d333b",
               textStyle: { color: "#e6edf3" },
               valueFormatter: v => v == null ? "—" : fmtPct(v, 1) },
    xAxis: { type: "category", data: years, axisLabel: { color: "#8b949e" } },
    yAxis: {
      axisLabel: { color: "#8b949e", formatter: v => (v * 100).toFixed(0) + "%" },
      splitLine: { lineStyle: { color: "#21262d" } },
    },
    series,
  }, { notMerge: true });
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

function renderTradesTable(data) {
  const trades = data.trades || [];
  document.getElementById("trades-count").textContent = trades.length;
  if (!trades.length) {
    $("#trades-note").textContent = "";
    $("#trades-table").innerHTML = "<p class='hint'>无交易记录</p>";
    return;
  }
  const cap = 500;   // DOM 渲染上限；净值图打点仍含全部事件
  $("#trades-note").textContent = trades.length > cap
    ? `共 ${trades.length} 笔，仅展示前 ${cap} 笔（净值图打点仍含全部）` : "";
  let html = `<table><thead><tr><th>#</th><th>日期</th><th>方向</th><th>标的</th>
              <th>权重变化</th><th>变动后权重</th><th>成交价</th><th>当日净值</th></tr></thead><tbody>`;
  trades.slice(0, cap).forEach((t, i) => {
    if (t.side === "deposit") {
      html += `<tr><td>${i + 1}</td><td>${t.date}</td><td style="color:#58a6ff">⊕ 入金</td><td>—</td>
               <td style="color:#58a6ff">${fmtMoney(t.amount ?? 0)}</td><td>—</td><td>—</td>
               <td>${t.nav != null ? fmtMoney(t.nav) : "—"}</td></tr>`;
    } else {
      const up = t.side === "buy";
      html += `<tr><td>${i + 1}</td><td>${t.date}</td><td class="${up ? "pos" : "neg"}">${up ? "▲ 买" : "▼ 卖"}</td>
               <td>${esc(t.ticker || "")}</td>
               <td class="${up ? "pos" : "neg"}">${fmtPct(t.delta, 1)}</td>
               <td>${fmtPct(t.weight_after, 0)}</td>
               <td>${t.price != null ? "$" + t.price.toFixed(2) : "—"}</td>
               <td>${t.nav != null ? fmtMoney(t.nav) : "—"}</td></tr>`;
    }
  });
  $("#trades-table").innerHTML = html + "</tbody></table>";
}

loadStrategies();
loadDefaultRange();
window.addEventListener("resize", () => {
  navChart && navChart.resize(); ddChart && ddChart.resize(); annualChart && annualChart.resize();
});
