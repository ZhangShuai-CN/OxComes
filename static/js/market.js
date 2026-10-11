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

function card(label, value, sub, cls = "", cardCls = "") {
  return `<div class="stat-card ${cardCls}"><div class="label">${label}</div>
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
    const p10 = breadth.pctile_10y != null ? `10年 ${breadth.pctile_10y}%位 · ` : "10年分位待数据 · ";
    cards.push(card("市场宽度", breadth.pctile_52w + "%位",
                    `${p10}21日 ${breadth.chg_21d}% · ${breadth.state_desc}`, cls));
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

// ── 波动率位置 + 牛形体制(2026-10-12;/api/market/indicators) ──

function volCls(p) {           // 分位→色:低分位=稳,高分位=警
  if (p == null) return "";
  return p >= 80 ? "warn" : (p <= 30 ? "pos" : "");
}

async function loadIndicators() {
  const d = await fetch("/api/market/indicators").then(r => r.json()).catch(() => null);
  if (!d || !d.ok) { return; }
  // 波动率卡:SPY/QQQ × 20日/63日,当前值+10年分位
  const vcards = [];
  for (const tk of ["SPY", "QQQ"]) {
    const v = (d.vol || {})[tk];
    if (!v || v.error) { continue; }
    for (const [key, name] of [["rv20", "20日实现波动率"], ["rv63", "63日实现波动率"]]) {
      const e = v[key];
      if (!e) { continue; }
      const pct = e.pctile_10y != null ? `10年分位 ${e.pctile_10y}%` : (e.note || "分位缺");
      vcards.push(card(`${tk} ${name}`, (e.value * 100).toFixed(1) + "%", pct, volCls(e.pctile_10y)));
    }
  }
  // 板块分化度(成份股分化的板块级近似):与指数时序波动正交——指数稳+分化高
  // = 头部集中护盘、内部两极分化的窄基结构市。色档由后端 level/cls 给
  // (极高红/高黄/中灰/低绿,按 10 年分位刻度,绝对值分布右偏不可直接比)
  const dsp = d.dispersion;
  if (dsp && dsp.available) {
    const rg = dsp.range_10y || {};
    const rgTxt = rg.mean != null
      ? ` · 十年均 ${ (rg.mean * 100).toFixed(1)}% / 区间 ${(rg.min * 100).toFixed(1)}~${(rg.max * 100).toFixed(1)}%`
      : "";
    // 整卡警示背景:高=黄卡、极高=红卡(与数字色同档),结构性警讯一眼可见
    const cardBg = dsp.level === "极高" ? "alert-bg" : (dsp.level === "高" ? "warn-bg" : "");
    vcards.push(card(`板块分化 · ${dsp.level || "—"}${dsp.level === "极高" ? "⚠" : ""}`,
                     (dsp.value * 100).toFixed(1) + "%",
                     `10年分位 ${dsp.pctile_10y}% · 63日均值${rgTxt}`, dsp.cls || "", cardBg));
  }
  const volPanel = $("#panel-vol");
  if (vcards.length) { $("#vol-cards").innerHTML = vcards.join(""); }
  else { volPanel.style.display = "none"; }
  if (d.as_of) { $("#vol-meta").textContent = `截至 ${d.as_of}`; }

  // 牛形体制卡:SPY/QQQ 判定 + 指标明细 + 最近历史年
  const rcards = [];
  for (const tk of ["SPY", "QQQ"]) {
    const r = (d.regime || {})[tk];
    if (!r) { continue; }
    const near = r.nearest ? ` · 最接近历史年:${r.nearest.year}` : "";
    const cls = r.label === "单边牛" ? "pos" : (r.label === "震荡牛" ? "warn" :
                 (r.label === "熊市" ? "neg" : ""));
    rcards.push(card(`${tk} · ${r.label}`, (r.ret_252 * 100).toFixed(1) + "%",
                     `R² ${r.r2} · ${r.dips} 次10%校正 · ER ${r.er}${near}`, cls));
  }
  const regimePanel = $("#panel-regime");
  if (rcards.length) { $("#regime-cards").innerHTML = rcards.join(""); }
  else { regimePanel.style.display = "none"; }
  if (d.as_of) { $("#regime-meta").textContent = `截至 ${d.as_of}`; }
}

// ── 估值位置(PE/EPS 十年月度分位,2026-10-12;multpl S&P500) ──

async function loadValuation() {
  const d = await fetch("/api/market/valuation").then(r => r.json()).catch(() => null);
  if (!d || !d.ok) { $("#panel-value").style.display = "none"; return; }
  const cards = [];
  const spy = d.spy || {};
  if (spy.pe) {
    const p = spy.pe;
    const rg = p.range_10y || {};
    const cardBg = p.cls === "neg" ? "alert-bg" : (p.cls === "warn" ? "warn-bg" : "");
    cards.push(card(`SPY PE(TTM)${p.level ? " · " + p.level : ""}`, p.value,
      `10年分位 ${p.pctile_10y}%${rg.mean ? ` · 十年均 ${rg.mean} / 区间 ${rg.min}~${rg.max}` : ""}` +
      ` · ${p.as_of}`, p.cls || "", cardBg));
  }
  if (spy.eps) {
    const e = spy.eps;
    const rg = e.range_10y || {};
    const yoy = e.yoy != null ? ` · 同比 ${e.yoy >= 0 ? "+" : ""}${e.yoy}%` : "";
    cards.push(card("SPY EPS(TTM)", "$" + e.value,
      `10年分位 ${e.pctile_10y}%${yoy}${rg.mean ? ` · 十年均 $${rg.mean}` : ""} · ${e.as_of}`,
      e.cls || ""));
  }
  if (spy.shiller) {
    const s = spy.shiller;
    const rg = s.range_10y || {};
    const cardBg = s.cls === "neg" ? "alert-bg" : (s.cls === "warn" ? "warn-bg" : "");
    cards.push(card(`Shiller PE${s.level ? " · " + s.level : ""}`, s.value,
      `10年分位 ${s.pctile_10y}% · 全史(1871)分位 ${s.pctile_full}%` +
      (rg.mean ? ` · 十年均 ${rg.mean}` : ""), s.cls || "", cardBg));
  }
  if (spy.erp) {
    const e = spy.erp;
    const cardBg = e.cls === "neg" ? "alert-bg" : (e.cls === "warn" ? "warn-bg" : "");
    cards.push(card(`ERP · ${e.level || ""}`, (e.value >= 0 ? "+" : "") + e.value + "pp",
      `E/P−10Y · 10年分位 ${e.pctile_10y}% · ${e.range_full_note} · ${e.as_of}`,
      e.cls || "", cardBg));
  }
  const v = $("#val-cards");
  if (cards.length) { v.innerHTML = cards.join(""); }
  else { $("#panel-value").style.display = "none"; return; }
  const meta = [];
  if ((d.spy || {}).pe) { meta.push(`S&P500 as of ${d.spy.pe.as_of}`); }
  if (d.qqq && !d.qqq.available) { meta.push("QQQ: " + (d.qqq.note || "源未接入")); }
  $("#val-meta").textContent = meta.join(" · ");
}

loadHeatmap();
loadMacroStats();
loadIndicators();
loadValuation();
window.addEventListener("resize", () => {
  chart && chart.resize();
  breadthChart && breadthChart.resize();
  vixChart && vixChart.resize();
});
