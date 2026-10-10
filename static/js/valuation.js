/* 估值页：multiples 卡 / Rule40 / 行业对比条形 / DCF 3×3 / 敏感性热力 */
"use strict";

const $ = (s) => document.querySelector(s);

$("#val-btn").addEventListener("click", run);
$("#symbol-input").addEventListener("keydown", e => { if (e.key === "Enter") run(); });

async function run() {
  const symbol = $("#symbol-input").value.trim().toUpperCase();
  if (!symbol) return;
  const growthRaw = $("#growth-input").value;
  const params = new URLSearchParams({ with_peers: $("#peers-toggle").value });
  if (growthRaw) params.set("fcf_growth", String(Number(growthRaw) / 100));
  const btn = $("#val-btn");
  btn.disabled = true; btn.textContent = "计算中…";
  $("#val-result").innerHTML = `<p class="hint">拉取 ${symbol} 财务数据（首次较慢：三表+profile+同行）…</p>`;
  try {
    const res = await fetch(`/api/valuation/${symbol}?${params}`);
    const d = await res.json();
    if (!d.ok) {
      $("#val-result").innerHTML = `<div class="synth-note">${d.error}</div>`;
      return;
    }
    render(d);
  } catch (e) {
    $("#val-result").innerHTML = `<div class="synth-note">请求失败: ${e.message}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "🔍 估值";
  }
}

function card(label, value, sub, cls = "") {
  return `<div class="stat-card"><div class="label">${label}</div>
          <div class="value ${cls}">${value ?? "—"}</div>
          <div class="sub">${sub ?? ""}</div></div>`;
}

function render(d) {
  const m = d.multiples || {};
  const r40 = d.rule40 || {};
  const ref = d.sector_ref;
  let html = "";
  html += `<div class="synth-note" id="val-notes"></div>`;

  // stat 卡
  const cards = [
    card(d.symbol + " " + (d.company || ""), "$" + (d.price ?? "—"),
         `${d.industry || ""}`,
         ""),
    card("PE (TTM)", m.pe ?? "—", m.pe_note ?? "市值/净利润，亏损时看 PS"),
    card("PS (TTM)", m.ps ?? "—", "市值/营收"),
    card("PB (TTM)", m.pb ?? "—", "市值/股东权益"),
    card(`Rule of 40${r40.and_line ? "（线=" + r40.and_line + "）" : ""}`,
         `${r40.score ?? "—"}<span style="font-size:12px;color:var(--text-dim)"> ${r40.verdict ?? ""}</span>`,
         `${r40.growth_pct ?? "—"}% 增速 + ${r40.margin_pct ?? "—"}% ${r40.margin_kind ?? ""}`),
  ];
  if (m.revenue_yoy != null) cards.push(card("营收 YoY", m.revenue_yoy + "%", m.peg ? `PEG ${m.peg}` : ""));
  if (ref) cards.push(card("行业 PE 参考", `${ref.pe_low}–${ref.pe_high}x`, `中位 ${ref.pe_mid}x`));
  if (d.dcf && d.dcf.base && d.dcf.base.value_per_share) {
    const b = d.dcf.base;
    cards.push(card("DCF 基准(r=11%,g=2.5%)", "$" + b.value_per_share.toFixed(1),
        `${b.verdict ?? ""}（上行 ${b.upside_pct}%）`,
        (b.upside_pct || 0) > 0 ? "pos" : "neg"));
  }
  html += `<div class="stat-row">${cards.join("")}</div>`;

  // 同行对比表
  if (d.peers && d.peers.n_ok >= 3) {
    html += `<div class="two-col"><div class="panel"><h3>同行倍数分布（${d.peers.industry}, n=${d.peers.n_ok}）</h3>`;
    html += `<div class="table-wrap"><table><thead><tr><th>标的</th><th>PE</th><th>PS</th><th>PB</th></tr></thead><tbody>`;
    for (const s of d.peers.samples) {
      const isSelf = s.symbol === d.symbol;
      html += `<tr class="${isSelf ? "warn" : ""}" style="${isSelf ? "font-weight:700;background:rgba(210,153,34,.12)" : ""}">
        <td>${s.symbol}${isSelf ? " ◀" : ""}</td><td>${s.pe ?? "—"}</td><td>${s.ps ?? "—"}</td><td>${s.pb ?? "—"}</td></tr>`;
    }
    html += `</tbody></table></div>`;
    const pd = d.peers.pe_dist;
    if (pd) html += `<p class="hint">PE: P25 ${pd.p25} · 中位 ${pd.p50} · P75 ${pd.p75} · 均值 ${pd.mean}</p>`;
    html += `</div>`;

    // 个股 vs 行业条形
    html += `<div class="panel"><h3>vs 行业分位</h3><div id="val-bars" class="chart-md"></div></div></div>`;
  } else if (d.peers) {
    html += `<p class="hint">同行分布样本不足（同行 FMP 拉取部分失败，或配额受限）</p>`;
  }

  // DCF 3×3
  if (d.dcf && d.dcf.sensitivity) {
    const s = d.dcf.sensitivity;
    html += `<div class="panel"><h3>DCF 敏感性（每股价值 $）—— r × 终值 g</h3>
             <p class="hint">增速假设：${d.dcf.growth_used ?? "—"}；悲观(高r低g) → 乐观(低r高g)</p>`;
    html += `<div class="table-wrap"><table><thead><tr><th>discount r ↓</th>`;
    for (const g of s.g_values) html += `<th>g ${(g * 100).toFixed(1)}%</th>`;
    html += `</tr></thead><tbody>`;
    s.r_values.forEach((r, i) => {
      html += `<tr><td>r ${(r * 100).toFixed(0)}%</td>`;
      for (const v of s.matrix[i]) {
        const cls = v != null && d.price ? (v > d.price ? "pos" : "neg") : "";
        html += `<td class="${cls}">${v != null ? "$" + v.toFixed(1) : "—"}</td>`;
      }
      html += `</tr>`;
    });
    html += `</tbody></table></div></div>`;
  } else if (d.dcf && d.dcf.error) {
    html += `<p class="hint">${d.dcf.error}</p>`;
  }

  // notes
  $("#val-result").innerHTML = html;
  const notes = (d.notes || []).join("<br>");
  $("#val-notes").style.display = notes ? "block" : "none";
  $("#val-notes").innerHTML = notes;

  renderBars(d);
}

function renderBars(d) {
  const el = document.getElementById("val-bars");
  if (!el || !d.peers || !d.peers.pe_dist) return;
  const m = d.multiples;
  const chart = echarts.init(el);
  const cats = ["PE", "PS", "PB"];
  const data = cats.map(c => {
    const dist = d.peers[c.toLowerCase() + "_dist"];
    return { cat: c, self: m[c.toLowerCase()], ...pick(dist) };
  });
  const series = [
    { name: "P25", type: "bar", stack: "d", data: data.map(x => x.p25 ?? 0),
      itemStyle: { color: "#30363d" }, barWidth: 46 },
    { name: "P50-P25", type: "bar", stack: "d",
      data: data.map(x => (x.p50 ?? x.p25 ?? 0) - (x.p25 ?? 0)),
      itemStyle: { color: "#3d444d" } },
    { name: "P75-P50", type: "bar", stack: "d",
      data: data.map(x => (x.p75 ?? x.p50 ?? 0) - (x.p50 ?? x.p25 ?? 0)),
      itemStyle: { color: "#4d5661" } },
    { name: "本股", type: "scatter",
      data: data.map((x, i) => [i, x.self ?? 0]), symbolSize: 18,
      itemStyle: { color: "#d29922" } },
  ];
  chart.setOption({
    animation: false, grid: { left: 60, right: 20, top: 30, bottom: 40 },
    legend: { top: 0, textStyle: { color: "#8b949e" } },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: cats, axisLabel: { color: "#8b949e" } },
    yAxis: { type: "value", axisLabel: { color: "#8b949e" },
             splitLine: { lineStyle: { color: "#21262d" } } },
    series,
  });
}

function pick(dist) {
  if (!dist) return {};
  const w = Math.max(dist.p75 - dist.p25, 0.05);
  return { p25: 0, p50: (dist.p50 - dist.p25) / w * 50,
           p75: (dist.p75 - dist.p25) / w * 50,
           raw: dist };
}
