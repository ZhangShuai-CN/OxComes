"""每日消息组装（markdown）：宽度/收益率/VIX/板块/事件 → 钉钉消息体。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from app.market import breadth as breadth_mod
from app.market import macro as macro_mod
from app.market import sectors as sectors_mod

logger = logging.getLogger(__name__)


def build_markdown() -> "tuple[str, str]":
    """返回 (title, markdown)。聚合各模块，任一失败不阻塞整体。"""
    lines = [f"### 📈 每日市场简报 {date.today().isoformat()}", ""]

    try:
        y = macro_mod.get_yields(1)
        card = (y.get("cards") or {}).get("10Y") or {}
        if card.get("value") is not None:
            chg = card.get("chg") or 0
            sign = "+" if chg >= 0 else ""
            c13 = ((y.get("cards") or {}).get("13W") or {}).get("value", "—")
            lines.append(f"- **10Y 美债**: {card['value']}%（日{sign}{chg}bp）· 13W {c13}%")
    except Exception as e:
        logger.warning("brief 收益率失败: %s", e)

    try:
        b = breadth_mod.get_breadth(120)
        if b.get("ok"):
            lines.append(f"- **市场宽度**: {b['state_desc']}（RSP/SPY 21日 {b['chg_21d']}%，52周 {b['pctile_52w']}%位）")
    except Exception as e:
        logger.warning("brief 宽度失败: %s", e)

    try:
        v = macro_mod.get_vix()
        if v.get("ok"):
            r = v.get("regime") or {}
            lines.append(f"- **VIX**: {v['value']} {r.get('emoji','')} {r.get('label','')} —— {r.get('desc','')}")
    except Exception as e:
        logger.warning("brief VIX 失败: %s", e)

    try:
        s = sectors_mod.rotation_snapshot()
        if s.get("ok"):
            secs = sorted(s["sectors"], key=lambda x: x["change_pct"], reverse=True)
            top3 = "、".join(f"{x['name']} +{x['change_pct']}%" if x["change_pct"] >= 0
                             else f"{x['name']} {x['change_pct']}%" for x in secs[:3])
            bot3 = "、".join(f"{x['name']} {x['change_pct']}%" for x in secs[-3:])
            lines.append(f"- **板块**: 领涨 {top3}")
            lines.append(f"-        领跌 {bot3}")
    except Exception as e:
        logger.warning("brief 板块失败: %s", e)

    # 未来 3 日关注事件（今日已存在的 events 表）
    try:
        from app.events import calendar as ev_cal
        start = date.today().isoformat()
        end = (date.today() + timedelta(days=3)).isoformat()
        events = ev_cal.query_events(start, end)
        if events:
            lines.append("")
            lines.append("**未来事件**")
            for e in events[:8]:
                lines.append(f"- {e['event_date'][5:]} [{e['category']}] {e['name'][:40]}")
    except Exception as e:
        logger.warning("brief 事件失败: %s", e)

    lines.append("")
    lines.append("> 本地数据源生成 · 仅供研究参考，不构成投资建议")
    return "每日市场简报", "\n".join(lines)


def push_daily_brief() -> dict:
    from app.notify.dingtalk import get_notifier
    notifier = get_notifier()
    if notifier is None:
        return {"ok": False, "error": "推送未开启：.env 中 NOTIFY_ENABLED=true 且配置 DINGTALK_TOKEN"}
    title, content = build_markdown()
    result = notifier.send_markdown(title, content)
    ok = result.get("errcode") == 0
    return {"ok": ok, "result": result.get("errmsg", "")}
