#!/usr/bin/env python3
"""命令行入口。

用法：
    python cli.py init                          # 初始化 .env + 数据库
    python cli.py sync [--tickers QQQ,TQQQ,...] # 增量同步行情（默认核心标的）
    python cli.py synthetic [--financing auto|0.05]
    python cli.py verify-tqqq [--financing auto|0.05]
    python cli.py backtest --strategy ma200_panic --tickers TQQQ \
                    --start 1999-01-01 --bench QQQ
    python cli.py strategies                    # 列出已注册策略
    python cli.py scan --strategy ma200_panic --grid 'ma_trend=[100;150;200]' --tickers TQQQ
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.data import store, synthetic  # noqa: E402
from app.data.universe import CORE_UNIVERSE  # noqa: E402


def cmd_init(args):
    env_example = settings.base_dir / ".env.example"
    env = settings.base_dir / ".env"
    if not env.exists():
        env.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✅ 已创建 .env（请按需填写 FMP_API_KEY 等，回测功能无需任何 Key）: {env}")
    else:
        print(f"ℹ .env 已存在: {env}")
    settings.ensure_dirs()
    db.init_db()
    print("✅ 数据库初始化完成:", settings.db_path)


def cmd_sync(args):
    from datetime import datetime
    from app.data.loaders import yfinance_loader
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else CORE_UNIVERSE
    db.init_db()
    started = datetime.now().isoformat(timespec="seconds")
    print(f"同步 {len(tickers)} 个标的 ...")
    ok, failed = 0, []
    for t in tickers:
        success, msg = store.sync_ticker(t, yfinance_loader.fetch_single)
        print(("  ✅ " if success else "  ❌ ") + msg)
        if success:
            ok += 1
        else:
            failed.append(t)
    status = "ok" if not failed else "partial"
    db.execute(
        "INSERT INTO sync_log(dataset, status, started_at, finished_at, message) VALUES(?,?,?,?,?)",
        ("daily_quotes", status, started, datetime.now().isoformat(timespec="seconds"),
         f"ok={ok} failed={len(failed)}: {','.join(failed)}"),
    )
    print(f"完成: 成功 {ok}/{len(tickers)}" + (f"，失败: {','.join(failed)}" if failed else ""))


def cmd_refresh(args):
    """强制全量重下（删除本地数据重拉）：Yahoo 限流恢复后用复权价替换 Stooq 未复权数据。"""
    from app.data.loaders import yfinance_loader
    from app.config import settings
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else CORE_UNIVERSE
    settings.daily_dir.mkdir(parents=True, exist_ok=True)
    for t in tickers:
        p = store.parquet_path(t)
        if p.exists():
            p.unlink()
        ok, msg = store.sync_ticker(t, yfinance_loader.fetch_single)
        print(("  ✅ " if ok else "  ❌ ") + msg)


def cmd_events(args):
    """扫描未来 N 日财报/IPO 事件入库（每日 06:41 调度自动执行）。"""
    from app.events import calendar as ev_cal
    db.init_db()
    print(f"扫描未来 {args.days} 天事件（Nasdaq 源，关注清单+大票分层过滤）...")
    result = ev_cal.update_events(days=args.days, include_ipo=not args.no_ipo)
    print(f"✅ 落库 {result['fetched']} 条（现有查询：）")
    from datetime import date, timedelta
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=args.days)).isoformat()
    rows = ev_cal.query_events(start, end)
    if not rows:
        print("  （暂无符合过滤条件的事件）")
    for r in rows[:15]:
        print(f"  {r['event_date']} [{r['category']}:{r['importance']}] "
              f"{r['name'][:40]} {r['description'][:24]}")


def cmd_funds(args):
    """13F 机构持仓：拉取/查看（EDGAR 直连，按人物精选 3 位投资人）。"""
    from app.data.loaders import sec_loader
    db.init_db()
    if args.sync:
        print("同步 3 位投资人的最新 13F（EDGAR…）")
        results = sec_loader.sync_all(force=args.force)
        for r in results:
            mark = "✅" if r.get("ok") else "❌"
            extra = (f"{r.get('quarter')} rows={r.get('rows')}" if r.get("ok")
                     else str(r.get("error"))[:50])
            print(f"  {mark} {r.get('manager', '?')}: {extra}")
    for p in sec_loader.person_snapshot(per_person=args.limit):
        if not p["has_data"]:
            print(f"\n{p['person']}({p['fund_cn']}): 暂无入库数据")
            continue
        print(f"\n{p['person']}({p['fund_cn']}) {p['quarter']} 重仓 top{len(p['items'])}:")
        for i in p["items"]:
            chg = i["change_shares"]
            chg_txt = (f"{chg/1e6:+.1f}M" if chg else
                       ("新进" if chg == 0 and i["report_date"] else "—"))
            print(f"  {i['name_cn']:<22} {(i['value_usd'] or 0)/1e9:>8.2f}B  {chg_txt:>10}  {i['report_date']}")


def _fin_cfg(args) -> synthetic.SyntheticConfig:
    cfg = synthetic.SyntheticConfig()
    f = getattr(args, "financing", None)
    if f and f != "auto":
        cfg.financing_mode = "fixed"
        cfg.fixed_financing_rate = float(f)
    return cfg


def cmd_synthetic(args):
    print("构建 TQQQ 合成序列（QQQ×3 日重置复利 + ^IRX 融资成本）...")
    pure = synthetic.build_pure_synthetic(_fin_cfg(args))
    spliced = synthetic.build_spliced(_fin_cfg(args))
    if pure is None or spliced is None:
        print("❌ 构建失败（QQQ/TQQQ/^IRX 数据不可用，先运行 sync）")
        sys.exit(1)
    print(f"✅ 纯合成序列: {pure.index[0].date()} → {pure.index[-1].date()} (n={len(pure)})")
    print(f"✅ 拼接序列:   {spliced.index[0].date()} → {spliced.index[-1].date()} (n={len(spliced)})")


def cmd_verify_tqqq(args):
    res = synthetic.verify(_fin_cfg(args))
    for line in res.details:
        print("  " + line)
    print(res.summary())
    sys.exit(0 if res.ok else 1)


def _fmt_perf(perf: dict) -> str:
    cagr = perf.get("cagr", 0)
    mdd = perf.get("max_drawdown", 0)
    sharpe = perf.get("sharpe")
    return (f"CAGR {cagr:+.2%}  MDD {mdd:.2%}  Calmar {perf.get('calmar', 0):.2f}  "
            f"Sharpe {sharpe:.2f}" if sharpe is not None
            else f"CAGR {cagr:+.2%}  MDD {mdd:.2%}")


def cmd_backtest(args):
    from app.backtest import runner
    params = json.loads(args.params) if args.params else {}
    tickers = [t.strip() for t in args.tickers.split(",")]
    benchmarks = [b.strip() for b in args.bench.split(",")] if args.bench else []
    result = runner.run_backtest(
        strategy_id=args.strategy,
        params=params,
        tickers=tickers,
        start=args.start,
        end=args.end,
        benchmarks=benchmarks,
        initial_capital=args.capital,
        rebalance_cost_bps=args.cost,
        use_synth_tqqq=args.tqqq_data,
        execution_price=args.execution,
    )
    if not result.ok:
        print(f"❌ 回测失败: {result.error}")
        sys.exit(1)
    print(f"\n策略: {args.strategy}  参数: {result.params}")
    print(f"区间: {result.start} → {result.end}  ({result.nav.index[0].date()} → {result.nav.index[-1].date()})")
    for n in result.notes:
        print("  " + n)
    if result.ret is not None:
        print(f"\n  组合  {_fmt_perf(result.perf or {})}")
    for name, m in result.bench_perf.items():
        print(f"  {name:<6} {_fmt_perf(m)}")
    print(f"  交易  调仓 {result.n_rebalance_days} 日 · 买 {result.n_buys} 笔 · 卖 {result.n_sells} 笔"
          + (f" · 定投入金 {sum(1 for t in result.trades if t.side == 'deposit')} 期"
             if any(t.side == "deposit" for t in result.trades) else ""))
    dd = (result.perf or {}).get("drawdown_top5") or []
    if dd:
        print("\nTop 回撤:")
        for d in dd[:3]:
            rec = f"恢复{d['recovery_days']}日" if d.get("recovery_days") else "未恢复"
            print(f"  {d['peak_date']} → {d['trough_date']}  {d['depth']:.1%}  ({rec})")


def cmd_strategies(args):
    from app.backtest.strategies import list_strategies
    for s in list_strategies():
        print(f"{s['id']:<22} {s['name']}")
        print(f"{'':<22} {s['description']}")
        keys = ", ".join(p["key"] for p in s["params"])
        print(f"{'':<22} params: {keys}\n")


def cmd_scan(args):
    from app.backtest import runner
    from app.backtest import params as pmod
    from app.backtest.strategies import get_strategy
    strategy = get_strategy(args.strategy)
    overrides = {}
    if args.grid:
        for kv in args.grid.split(","):
            k, v = kv.split("=")
            try:
                overrides[k] = json.loads(v)
            except json.JSONDecodeError:
                overrides[k] = [s.strip() for s in v.strip("[]").split(";")]
    combos = pmod.build_grid(strategy, overrides)
    tickers = [t.strip() for t in args.tickers.split(",")]
    print(f"网格扫描 {args.strategy}: {len(combos)} 组合")
    rows = []
    for i, combo in enumerate(combos, 1):
        r = runner.run_backtest(args.strategy, combo, tickers,
                                start=args.start, end=args.end,
                                benchmarks=None, use_synth_tqqq=args.tqqq_data)
        if r.ok and r.perf:
            rows.append((combo, r.perf))
        if i % 10 == 0:
            print(f"  ... {i}/{len(combos)}")
    rows.sort(key=lambda x: (x[1].get("sharpe") if x[1].get("sharpe") is not None else -99), reverse=True)
    print(f"\n{'参数':<44} {'CAGR':>9} {'MDD':>9} {'Sharpe':>8}")
    for combo, perf in rows[:20]:
        print(f"{str(combo):<44} {perf['cagr']:>8.2%} {perf['max_drawdown']:>8.1%} "
              f"{(perf.get('sharpe') or 0):>8.2f}")


def cmd_fetch_assets(args):
    """下载前端 vendored 资源（ECharts）。幂等；已存在则跳过。"""
    import urllib.request
    vendor = settings.base_dir / "static" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    assets = [
        ("echarts.min.js",
         "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"),
    ]
    for fname, url in assets:
        target = vendor / fname
        if target.exists() and target.stat().st_size > 100_000:
            print(f"✅ {fname} 已存在 ({target.stat().st_size // 1024}KB)")
            continue
        print(f"下载 {url} ...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as resp, open(target, "wb") as f:
                f.write(resp.read())
            print(f"✅ {fname} → {target.stat().st_size // 1024}KB")
        except Exception as e:
            print(f"❌ {fname} 下载失败: {e}（可自行放置该文件到 {vendor}/）")
            sys.exit(1)


def main():
    settings.ensure_dirs()
    logfile = settings.log_dir / "cli.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr),
                  logging.FileHandler(logfile, encoding="utf-8", mode="a")],
    )
    p = argparse.ArgumentParser(description="OxComes CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("fetch-assets").set_defaults(fn=cmd_fetch_assets)

    sp = sub.add_parser("events")
    sp.add_argument("--days", type=int, default=7)
    sp.add_argument("--no-ipo", action="store_true")
    sp.set_defaults(fn=cmd_events)

    sp = sub.add_parser("fund-holdings")
    sp.add_argument("--sync", action="store_true", help="同时拉取最新 13F")
    sp.add_argument("--force", action="store_true", help="忽略季度幂等强制重拉")
    sp.add_argument("--limit", type=int, default=15)
    sp.set_defaults(fn=cmd_funds)

    sp = sub.add_parser("sync")
    sp.add_argument("--tickers", default=None, help="逗号分隔，默认核心标的")
    sp.set_defaults(fn=cmd_sync)

    sp = sub.add_parser("refresh")
    sp.add_argument("--tickers", default=None, help="逗号分隔，默认核心标的")
    sp.set_defaults(fn=cmd_refresh)

    sp = sub.add_parser("synthetic")
    _add_fin(sp)
    sp.set_defaults(fn=cmd_synthetic)

    sp = sub.add_parser("verify-tqqq")
    _add_fin(sp)
    sp.set_defaults(fn=cmd_verify_tqqq)

    sp = sub.add_parser("backtest")
    sp.add_argument("--strategy", required=True)
    sp.add_argument("--params", default=None, help='JSON，如 \'{"ma":200}\'')
    sp.add_argument("--tickers", required=True, help="逗号分隔标的")
    sp.add_argument("--start", default=None, help="默认=数据最早可用日")
    sp.add_argument("--end", default=None, help="默认=数据最新日")
    sp.add_argument("--bench", default=None, help="基准对照，逗号分隔")
    sp.add_argument("--capital", type=float, default=10_000)
    sp.add_argument("--cost", type=float, default=2.0, help="换手成本 bps")
    sp.add_argument("--tqqq-data", choices=["auto", "always", "never"], default="auto",
                    help="TQQQ 数据模式: auto=回测起点早于2010自动用合成")
    sp.add_argument("--execution", choices=["close", "next_open"], default="next_open",
                    help="成交价口径: next_open=t+1开盘成交(外站口径,默认) | close=收盘结算")
    sp.set_defaults(fn=cmd_backtest)

    sub.add_parser("strategies").set_defaults(fn=cmd_strategies)

    sp = sub.add_parser("scan")
    sp.add_argument("--strategy", required=True)
    sp.add_argument("--grid", default=None, help='如 \'ma=[50;100;200]\'（分号分隔）')
    sp.add_argument("--tickers", required=True)
    sp.add_argument("--start", default=None)
    sp.add_argument("--end", default=None)
    sp.add_argument("--tqqq-data", choices=["auto", "always", "never"], default="auto")
    sp.set_defaults(fn=cmd_scan)

    args = p.parse_args()
    settings.ensure_dirs()
    args.fn(args)


def _add_fin(sp):
    sp.add_argument("--financing", default="auto",
                    help="auto=用^IRX历史利率 | 固定值如 0.05")


if __name__ == "__main__":
    main()
