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

// ---------- 主标的变更联动（标的先行的核心：chips 高亮 → 数据窗口 → 策略参数/基准跟随） ----------
const DEFAULT_TICKER_HINT =
  "策略所需的信号/防御资产（如 QQQ 的 200 日均线）由系统根据策略参数自动加载，无需手动输入。对照用资产放「基准对照」框。";

function syncChips() {
  const t = $("#tickers").value.trim().toUpperCase();
  document.querySelectorAll("#ticker-chips .chip").forEach(ch =>
    ch.classList.toggle("active", ch.dataset.t === t));
}

function onTickerChange() {
  document.getElementById("ticker-hint").textContent = DEFAULT_TICKER_HINT;  // 清多标的警告等临态文案
  // 基准跟随标的：换主标的后旧基准失效，重置为新主标的死拿
  benchDirty = false;
  // 参数记忆按标的独立(策略绑标的),切标的=切槽,各自的手改原样保留
  // TQQQ 数据模式只对 TQQQ 主标的有意义
  document.getElementById("tqqq-mode-row").style.display = singleTicker() === "TQQQ" ? "" : "none";
  syncChips();
  loadDefaultRange();
  renderParamForm();
}

// 默认日期 = 本地数据全范围（起始/结束的默认值，直接可跑完整窗口）
let loadRangeSeq = 0;    // 连续换标的时丢弃过期响应
let datesDirty = false;  // 用户手改过日期后，换标的保留窗口（同窗口跨标的对比是核心用法）
async function loadDefaultRange() {
  const t = singleTicker();
  if (!t) return;
  const seq = ++loadRangeSeq;
  try {
    const res = await fetch(`/api/backtest/range?tickers=${encodeURIComponent(t)}`);
    if (!res.ok) return;
    const r = await res.json();
    if (seq !== loadRangeSeq) return;
    const per = Object.entries(r.per_ticker || {})
      .map(([k, v]) => `${k}: ${v.start} → ${v.end}`).join(" · ");
    if (datesDirty) {
      document.getElementById("range-hint").textContent =
        `${t} 本地数据全范围：${r.start} → ${r.end}  [${per}]（当前窗口为手动设置，未覆写）`;
      return;
    }
    $("#start").value = r.start;
    $("#end").value = r.end;
    document.getElementById("range-hint").textContent =
      `默认已回填 ${t} 本地数据全范围：${r.start} → ${r.end}  [${per}]`;
  } catch (e) { /* 留空 = 引擎自动用全范围 */ }
}

function currentStrategy() {
  return catalog.find(s => s.id === $("#strategy-select").value);
}

// 默认基准对照 = 主标的自己（策略与标的绑定：任何策略都与「死拿该标的」比较，
// 这是择时评估最核心的对照；TQQQ 的策略对照 TQQQ、QQQ 的策略对照 QQQ）。
// 需要额外标杆（如加 QQQ）可手动在基准框里补。
let benchDirty = false;  // 用户手改过基准后保留（换策略不覆写；换主标的必重置）

function defaultBenchmarks() {
  return singleTicker() || "";
}

// per-ticker 策略预设。QQQ 共识版（2026-10-11 用户拍板）：牛熊线 200 出场 +
// 恐慌线 400 抄底 + 对牛熊线乖离 20% 止盈/10% 回调买回（对称乖离档；研究版
// 300/400+锚150@1.15/1.02 曾被用户判定过拟合弃用）。TQQQ 无预设 = ParamSpec
// 默认(200/500 + 锚200@1.5/1.2 经典过热)。
// SPY 预设（2026-10-11 网格研究定稿）：牛熊线 200（SPY 网格山脊 + 媒体语境
// 牛熊线本尊）+ 恐慌线 850（≈3.4 年深熊抄底线：参数面 300-850 平坦，850 的
// 增益全在慢熊防守——2008 -26.2% vs 死拿 -39.4%、2022 -7.2% vs -20.0%）+
// 对牛熊线乖离 15% 止盈/10% 回调（SPY 历史最大拉伸 ×1.205、×1.2 以上仅 5 天
// =闲置档，15% 才是实的；(QQQ 版同款 5pp 滞回带)。全窗口 8.20%/MDD -50.4%,
// 死拿 7.25%/-56.7%——SPY 择时增益(+0.95pp)低于 QQQ(+3.6pp)是结构性的,
// 1x 宽基波动浅、逃生价值小。
const PER_TICKER_PRESETS = {
  QQQ: { ma_trend: 200, ma_panic: 400, oh_anchor: 200, oh_sell_mult: 1.2, oh_rebuy_mult: 1.1,
         overheat: "on" },
  SPY: { ma_trend: 200, ma_panic: 850, oh_anchor: 200, oh_sell_mult: 1.15, oh_rebuy_mult: 1.10,
         overheat: "on" },
};

// 用户手改过的参数值按 [主标的][key] 两级记忆：策略绑标的、参数跟着标的走，
// 各标的的调参互不干扰；手改 > per-ticker 预设 > ParamSpec 默认
let paramEdits = {};

function paramValue(p, mainTk) {
  const edits = paramEdits[mainTk] || {};
  if (p.key in edits) return edits[p.key];
  if (/_ticker$/.test(p.key)) return mainTk || p.default;   // 信号/持仓资产跟随主标的
  const preset = PER_TICKER_PRESETS[mainTk] || {};
  if (p.key in preset) return preset[p.key];
  return p.default ?? "";
}

function renderParamForm() {
  const s = currentStrategy();
  if (!s) return;
  $("#strategy-desc").textContent = s.description;
  if (!benchDirty) $("#benchmarks").value = defaultBenchmarks();
  const mainTk = singleTicker();
  $("#param-form").innerHTML = s.params.map(p => {
    let val = paramValue(p, mainTk);
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
  // 策略区两级标题：h3「策略」+ h4「{主标的} · {策略名}」（与基准分组 h4 同层级同字号）；
  // 过热增强开启时在策略名后附完整增强语义（与参数下拉的选项文案一致）
  const sInfo = catalog.find(s => s.id === data.strategy_id);
  const sT = document.getElementById("strategy-title");
  const sSub = document.getElementById("strategy-subtitle");
  const mainTk = (data.tickers && data.tickers[0]) || "";
  const sName = sInfo ? sInfo.name : "策略";
  const qp = data.params || {};
  const overheatOn = qp.overheat === "on";
  // 过热副标题随实参生成(锚均线/倍数参数化后不再写死 200MA+50%)
  const ohTxt = overheatOn
    ? ` + (过热线 MA${qp.oh_anchor ?? 200}×${qp.oh_sell_mult ?? 1.5}止盈 · 过热回调线 MA${qp.oh_anchor ?? 200}×${qp.oh_rebuy_mult ?? 1.2}买回)`
    : "";
  sSub.textContent = `${mainTk} · ${sName}${ohTxt}`;
  sT.style.display = "block";
  sSub.style.display = "block";
  // 未增强基线（后端 overheat=on 时同窗口配跑）：放策略区下方，组名=原策略名
  const bl = (data.baseline_policy && data.baseline_policy.ok) ? data.baseline_policy : null;
  const blWrap = $("#baseline-cards");
  if (bl) {
    const blp = bl.perf || {};
    const blv = (bl.nav || {}).values || [];
    const blInit = blp.invested ?? (blv.length ? blv[0] : capitalInput);
    const blIrr = (blp.invested != null && blp.xirr != null) ? blp.xirr : (blp.cagr ?? 0);
    blWrap.innerHTML = `<div class="bench-group"><h4>${esc(mainTk)} · ${esc(sName)}</h4>
      <div class="stat-row">${cardsHtml([
        { label: "年化 IRR", value: fmtPct(blIrr, 2), cls: blIrr >= 0 ? "pos" : "neg",
          sub: `总收益 ${fmtTotal(blp.total_return ?? 0)} / ${(blp.years ?? 0).toFixed(1)} 年 · 同开窗对照` },
        { label: "期末账户总额",
          value: blv.length ? fmtMoney(blv[blv.length - 1]) : "—",
          cls: (blv.length && blv[blv.length - 1] >= blInit) ? "pos" : "neg",
          sub: `初始 ${fmtMoney(blInit)} · ${(blv.length ? blv[blv.length - 1] / blInit : 0).toFixed(1)}x` },
        { label: "最大回撤", value: fmtPct(blp.max_drawdown ?? 0, 1), cls: "neg",
          sub: `Calmar ${fmtNum(blp.calmar ?? 0)} · 调仓 ${bl.n_rebalance_days} 日` },
      ])}</div></div>`;
    blWrap.style.display = "block";
  } else {
    blWrap.style.display = "none";
    blWrap.innerHTML = "";
  }

  // 基准对照卡：每个基准一个分组标题 + 独占一行（Q 标的对照互不混排）
  const benchRow = $("#bench-cards");
  const benches = Object.entries(data.benchmarks || {});
  document.getElementById("bench-title").style.display = benches.length ? "block" : "none";
  const groupCards = (label, cards, subtitle) =>
    `<div class="bench-group"><h4>${esc(label)} · ${subtitle}</h4>
     <div class="stat-row">${cardsHtml(cards)}</div></div>`;
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
    return groupCards(name, cards, bType);
  }).join("");

  renderNavChart(data);
  renderDDChart(data);
  renderAnnualChart(data);
  renderDDTable(p);
  renderTradesTable(data);
}

let navChart = null, ddChart = null, annualChart = null;

// ---------- 系列视觉语义（折线图与年度柱状图共用的单点权威） ----------
// 系列名与颜色一律在此定义并由各系列显式引用，不依赖 ECharts palette 按序轮转
// （palette 槽位按系列序号消耗，显式设色的系列也会占槽）——由此系列顺序（排版）
// 与配色（语义）彻底解耦：调整顺序不会让颜色漂移。
const STRATEGY_COLOR = "#58a6ff";                // 主策略：蓝（两图主曲线/主柱同色）
const BASELINE_COLOR = "rgba(88,166,255,0.45)";  // 未增强基线：同色系半透明（同一策略的另一种形态）
const BENCH_COLORS = ["#d29922", "#a371f7", "#db61a2", "#39c5cf"];  // 基准轮转：黄/紫/粉/青

// 系列命名（两图统一）：配跑基线时 主=「策略.增强」基线=「策略」；
// 无基线时视有无基准叫「策略」/「组合」。返回的顺序就是两图的系列语义顺序。
function seriesMeta(data) {
  const hasBl = !!(data.baseline_policy && data.baseline_policy.ok);
  const hasBench = Object.keys(data.benchmarks || {}).length > 0;
  return {
    hasBl, hasBench,
    mainName: hasBl ? "策略.增强" : (hasBench ? "策略" : "组合"),
    baselineName: "策略",
  };
}

function renderNavChart(data) {
  navChart = navChart || echarts.init($("#nav-chart"));
  // 系列顺序 = 语义顺序（与年度柱状图一致）：主策略 → 基线（若有） → 基准；
  // 颜色全部显式指定（见系列视觉语义常量区），与顺序解耦
  const { hasBl, mainName, baselineName } = seriesMeta(data);
  const series = [{
    name: mainName,
    type: "line", data: data.nav.values, showSymbol: false,
    lineStyle: { width: 2, color: STRATEGY_COLOR },
  }];
  // 未增强基线（overheat=on 配跑）：与主策略同色系虚线半透明（同策略的另一种形态）
  if (hasBl) {
    series.push({
      name: baselineName, type: "line", data: data.baseline_policy.nav.values, showSymbol: false,
      lineStyle: { width: 1.5, type: "dashed", color: STRATEGY_COLOR, opacity: 0.55 },
    });
  }
  Object.entries(data.benchmarks || {}).forEach(([name, b], i) => {
    series.push({
      name, type: "line", data: b.nav.values, showSymbol: false,
      lineStyle: {
        width: 1.5, type: "dashed", opacity: 0.75,
        color: BENCH_COLORS[i % BENCH_COLORS.length],
      },
    });
  });
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
  const bl = (data.baseline_policy && data.baseline_policy.ok) ? data.baseline_policy : null;
  const benches = Object.entries(data.benchmarks || {});
  // 年份 = 策略/未增强基线/全部基准年度收益的并集（有数据的年份才出柱）
  const years = [...new Set([
    ...Object.keys(ar),
    ...(bl ? Object.keys(bl.perf.annual_returns || {}) : []),
    ...benches.flatMap(([, b]) => Object.keys(b.perf.annual_returns || {})),
  ])].sort();
  annualChart = annualChart || echarts.init($("#annual-chart"));
  // 系列顺序 = 语义顺序（与净值折线图一致）：主策略 → 基线（若有） → 基准；
  // 颜色全部显式指定（见系列视觉语义常量区），与顺序解耦；
  // 正负由柱向表达（0 轴上下），颜色只负责区分系列
  const { mainName, baselineName } = seriesMeta(data);
  const series = [{
    name: mainName, type: "bar",
    data: years.map(y => ar[y] ?? null), barMaxWidth: 34,
    itemStyle: { color: STRATEGY_COLOR },
  }];
  if (bl) {
    series.push({
      name: baselineName, type: "bar",
      data: years.map(y => (bl.perf.annual_returns || {})[y] ?? null),
      itemStyle: { color: BASELINE_COLOR }, barMaxWidth: 34,
    });
  }
  benches.forEach(([name, b], i) => {
    series.push({
      name, type: "bar",
      data: years.map(y => (b.perf.annual_returns || {})[y] ?? null),
      itemStyle: { color: BENCH_COLORS[i % BENCH_COLORS.length] }, barMaxWidth: 34,
    });
  });
  annualChart.setOption({
    animation: false,
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
  // 未增强基线存在时：日期不在基线交易日的买卖 = 过热增强新增的成交（「过热」标签区分）
  const bl = (data.baseline_policy && data.baseline_policy.ok) ? data.baseline_policy : null;
  const baseDates = bl ? new Set((bl.trades || []).map(t => t.date)) : null;
  const extraTag = (t) => (baseDates && t.side !== "deposit" && !baseDates.has(t.date))
    ? ' <span class="oh-extra">过热</span>' : "";
  let extraCnt = 0;
  if (baseDates) extraCnt = trades.filter(t => t.side !== "deposit" && !baseDates.has(t.date)).length;
  if (!trades.length) {
    $("#trades-note").textContent = "";
    $("#trades-table").innerHTML = "<p class='hint'>无交易记录</p>";
    return;
  }
  const cap = 500;   // DOM 渲染上限；净值图打点仍含全部事件
  let note = trades.length > cap
    ? `共 ${trades.length} 笔，仅展示前 ${cap} 笔（净值图打点仍含全部）。` : "";
  if (bl) {
    note += `原版策略（图表「策略」）同期 ${bl.n_rebalance_days} 次调仓 · 过热增强新增 ${extraCnt} 笔（标「过热」）。`;
  }
  $("#trades-note").textContent = note.trim();
  let html = `<table><thead><tr><th>#</th><th>日期</th><th>方向</th><th>标的</th>
              <th>权重变化</th><th>变动后权重</th><th>成交价</th><th>当日净值</th></tr></thead><tbody>`;
  trades.slice(0, cap).forEach((t, i) => {
    if (t.side === "deposit") {
      html += `<tr><td>${i + 1}</td><td>${t.date}</td><td style="color:#58a6ff">⊕ 入金</td><td>—</td>
               <td style="color:#58a6ff">${fmtMoney(t.amount ?? 0)}</td><td>—</td><td>—</td>
               <td>${t.nav != null ? fmtMoney(t.nav) : "—"}</td></tr>`;
    } else {
      const up = t.side === "buy";
      html += `<tr><td>${i + 1}</td><td>${t.date}</td><td class="${up ? "pos" : "neg"}">${up ? "▲ 买" : "▼ 卖"}${extraTag(t)}</td>
               <td>${esc(t.ticker || "")}</td>
               <td class="${up ? "pos" : "neg"}">${fmtPct(t.delta, 1)}</td>
               <td>${fmtPct(t.weight_after, 0)}</td>
               <td>${t.price != null ? "$" + t.price.toFixed(2) : "—"}</td>
               <td>${t.nav != null ? fmtMoney(t.nav) : "—"}</td></tr>`;
    }
  });
  $("#trades-table").innerHTML = html + "</tbody></table>";
}

// ---------- 交互接线 ----------
$("#tickers").addEventListener("change", onTickerChange);
document.querySelectorAll("#ticker-chips .chip").forEach(ch =>
  ch.addEventListener("click", () => {
    if (singleTicker() === ch.dataset.t) return;  // 已选中
    $("#tickers").value = ch.dataset.t;
    onTickerChange();
  }));
// 参数记忆：param-form 容器级委托（renderParamForm 重建内部 DOM 不影响监听）,按当前主标的记槽
$("#param-form").addEventListener("change", (e) => {
  const key = e.target.dataset && e.target.dataset.param;
  if (key) (paramEdits[singleTicker()] = paramEdits[singleTicker()] || {})[key] = e.target.value;
});
// 用户手改过日期后标记：换主标的时窗口不再被覆写（同窗口跨标的对比）
$("#start").addEventListener("change", () => { datesDirty = true; });
$("#end").addEventListener("change", () => { datesDirty = true; });
// 手改过基准后保留（换策略不覆写；换主标的必重置，见 onTickerChange）
$("#benchmarks").addEventListener("change", () => { benchDirty = true; });

loadStrategies();
loadDefaultRange();
window.addEventListener("resize", () => {
  navChart && navChart.resize(); ddChart && ddChart.resize(); annualChart && annualChart.resize();
});
