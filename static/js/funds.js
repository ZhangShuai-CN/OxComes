/* /funds 机构持仓页:按人物展示 13F 重仓(中文机构名/持仓名)。
   佩洛西/Trump 非 13F 主体,由模板静态「待接入」说明,本 JS 不请求其数据。
   无假数据铁律:人物无入库数据 → 显示「待首次同步」,不渲染空表。 */
"use strict";
const $ = (s) => document.querySelector(s);

async function loadFunds() {
  const d = await fetch("/api/admin/fund-holdings?per_person=10").then(r => r.json()).catch(() => null);
  if (!d || !d.ok || !d.persons || !d.persons.length) {
    $("#persons").innerHTML = "<p class='hint'>机构持仓数据加载失败</p>";
    return;
  }
  $("#persons").innerHTML = d.persons.map(p => {
    const head = `<h2>👤 ${p.person} <span class="hint">${p.fund_cn} · ${p.fund_en} · ${p.quarter || "待同步"}</span></h2>`;
    if (!p.has_data || !p.items.length) {
      return `<section class="panel">${head}
        <p class="hint">暂无入库数据——点上方「同步 3 位投资人 13F」后刷新(EDGAR 直连偶发超时)。</p>
        </section>`;
    }
    const rows = p.items.map(i => {
      const chg = i.change_shares;
      const chgTxt = chg == null ? "—" :
        (chg > 0 ? `+${(chg / 1e6).toFixed(1)}M` : `${(chg / 1e6).toFixed(1)}M`);
      const nameCell = i.name_cn !== i.issuer
        ? `${i.name_cn} <span class="hint">${i.issuer}</span>` : i.name_cn;
      return `<tr><td>${nameCell}</td>
        <td>${((i.value_usd || 0) / 1e9).toFixed(2)}B</td>
        <td class="${chg > 0 ? "pos" : (chg < 0 ? "neg" : "")}">${chgTxt}</td>
        <td>${i.report_date || ""}</td></tr>`;
    }).join("");
    return `<section class="panel">${head}
      <div class="table-wrap"><table>
      <thead><tr><th>持仓</th><th>市值</th><th>vs 上季</th><th>报告期</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      <p class="hint">变化量 vs 上一季(按发行人名近似配对;「新进」= 上季无记录)。</p>
      </section>`;
  }).join("");
}

async function syncFunds() {
  const st = $("#funds-status");
  st.textContent = "拉取中(3 位投资人 × submissions+infotable…通常 15-40 秒)";
  try {
    const d = await fetch("/api/admin/fund-holdings/sync", { method: "POST" }).then(r => r.json());
    const okN = (d.results || []).filter(r => r.ok).length;
    st.textContent = `完成 ${okN}/${(d.results || []).length}`;
  } catch (e) { st.textContent = "❌ " + e.message; }
  loadFunds();
}

loadFunds();
