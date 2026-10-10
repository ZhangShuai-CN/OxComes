# OxComes

本地运行的美股与 BTC 投资研究平台：**回测（TQQQ 含 2000 年泡沫）、板块轮动、每日消息、估值、机构持仓**。

> 无假数据铁律：任何数据拉不到或校验不过 → 该卡片/面板直接不显示，绝无占位数值、绝无分段常数兜底。

## 功能

| 模块 | 状态 | 说明 |
|---|---|---|
| 回测工作台 | ✅ Phase 1 | 4 策略（买入持有/定投/均线两类：ma200_panic、ma_cross）+ 自定义注册；TQQQ 合成（QQQ×3 + NDX 桥接自动启用） |
| 板块轮动 | ✅ Phase 2 | 11 板块涨跌/动量热力/轮动象限/RS 曲线（/sectors） |
| 每日消息看板 | ✅ 2026-10-10 | 分类消息中心:财报/IPO/科技大会/投资者日（/ 首页,过去 30 天+未来 7 天,历史一键回填） |
| 美股总览 | ✅ Phase 2 | 市场热力图 + 市场宽度(RSP/SPY 代理) + VIX（/market） |
| 国债 | ✅ 2026-10-10 | 10Y/30Y/13W 收益率卡 + 10Y 走势（/rates，真源财政部/无兜底） |
| 财报事件 | ✅ Phase 2 | Nasdaq API（大陆可达实测）+ 关注清单/千亿市值分层过滤 |
| 钉钉推送 | ✅ Phase 2 | .env 开关（默认关），页面一键推送 + 每日 07:53 调度 |
| 估值 | ✅ Phase 3 | PE/PB/PS 行业对比、Rule of 40、DCF/SOTP/敏感性（FMP stable API） |
| 机构持仓 13F | ✅ 2026-10-10 | SEC EDGAR 直连，按人物：巴菲特/段永平/德鲁肯米勒（/funds，中文机构名+持仓名；佩洛西/Trump 待接入） |
| 加密总览 | ✅ 2026-10-10 | 币价多周期卡 + 恐惧贪婪指数仪表/走势（/crypto） |
| 加密回测 | ✅ 2026-10-10 | F&G×BTC 择时回测（/crypto/backtest） |
| 网格扫描 UI | ✅ Phase 4 | /backtest 折叠区一键扫描（Sharpe Top20 排序） |
| 管理页 | ✅ Phase 4 | /admin：数据新鲜度/调度/配额/手动同步 |
| IPO 日历 | ✅ Phase 4 | Nasdaq 月度端点（募资 ≥$500M 全收 / ≥$100M 科技收） |
| FedWatch 概率 | ⏸ 已下线 | 无真实源即不显示（ZQ=F 接入后恢复；此前显示 13W 近似基准已移除） |

## 快速开始

```bash
# 1. 环境（Python 3.10 + uv）
uv venv --python python3.10 .venv
uv pip install -r requirements.txt --python .venv/bin/python
source .venv/bin/activate

# 2. 初始化（生成 .env 模板 + 建库；回测功能无需任何 API Key）
python cli.py init

# 3. 下载核心行情（QQQ/TQQQ/板块ETF/BTC-USD/^IRX 等，首次约 2 分钟）
python cli.py sync

# 4. 构建 TQQQ 合成序列 + 校验（重叠期相关系数≥0.999、年化漂移≤2%）
python cli.py synthetic
python cli.py verify-tqqq

# 5. 启动网站
./run.sh          # http://127.0.0.1:8300
```

## 命令行示例

```bash
# TQQQ 自 1999 以来（合成+真实拼接）vs 200MA趋势+500MA恐慌抄底 vs QQQ —— 本平台的标志性用例
python cli.py backtest --strategy ma200_panic \
    --params '{"signal_ticker":"QQQ","holding_ticker":"TQQQ"}' \
    --tickers TQQQ --start 1999-03-10 --bench QQQ,SPY

# 定投 BTC
python cli.py backtest --strategy dca --params '{"amount":100,"freq":"weekly"}' \
    --tickers BTC-USD --start 2014-09-01

# 均线参数网格扫描
python cli.py scan --strategy ma200_panic --grid 'ma_trend=[100;150;200;250]' \
    --tickers TQQQ --start 2000-01-01

# 查看全部策略
python cli.py strategies
```

## Phase 2 使用

```bash
# 板块/每日消息数据（11 板块 ETF + SPY + RSP 宽度代理）
python cli.py sync --tickers XLK,XLF,XLE,XLI,XLY,XLP,XLV,XLB,XLU,XLC,XLRE,SPY,RSP

# 财报事件扫描（写入 events 表，页面首次访问也会自动拉取）
python cli.py events --days 7      # 或者直接访问首页

# 推送到钉钉（需 .env: NOTIFY_ENABLED=true + DINGTALK_TOKEN/SECRET）
#    页面上「推送到钉钉」按钮，或每日 07:53 调度自动推送
```

- **每日消息**(`/`)：分类消息中心——财报 / IPO / 科技大会 / 投资者日四块,
  窗口 = 过去 30 天 + 未来 7 天;首次点「回填最近 1 个月历史消息」幂等补库
  (Nasdaq 财报逐日+IPO 按月)。「科技大会」「投资者日」暂无免费事件源,分类块
  只显示来源说明,不造数据。今日板块涨跌已迁出（板块轮动页 /sectors 已有）
- **美股总览**(`/market`)：市场热力图、市场宽度（RSP/SPY 等权-市值加权代理）、
  VIX 恐慌指数与近一年走势（仅当日快照时只显示卡片）。
  「最近交易日」视图日期强制对齐:数据未同步到基准日(as_of=标的最后一K日最大值)
  的标的隐藏并在标题披露明细,杜绝 2026-10-10 的"今日显示昨日"混日事故;
  每日 07:13 同步失败的标的 08:43 自动重试
- **国债**(`/rates`)：10Y/30Y/13W 收益率卡 + 10Y−13W 利差 + 10Y 历史走势
- **板块轮动**(`/sectors`)：当日涨跌、1d~12m 动量热力、轮动象限散点、RS vs SPY 多选曲线
- 财报事件按「关注清单 + 市值 ≥ $1000 亿」分层过滤，避免小票刷屏
  （清单可编辑：`app/events/calendar.py DEFAULT_WATCHLIST`）

## Phase 4 使用

```bash
# 13F 机构持仓（EDGAR 直连，按人物 3 家：Berkshire/H&H/Duquesne）
python cli.py fund-holdings --sync        # 拉取最新季度（幂等，季度级跳过）
python cli.py fund-holdings --sync --force   # 强制重拉
```

- **/funds**：按人物展示——巴菲特(伯克希尔·哈撒韦)、段永平(H&H 国际投资)、
  德鲁肯米勒(杜肯家族办公室)各自最新季度重仓 top10,机构名/持仓名中文显示
  （未收录译名的发行人显示英文原名,绝不编造）；佩洛西/Trump 数据类型不同
  （国会 PTR / 政府 OGE 披露,非 13F 主体）,页面留待接入说明
- **13F 机制**：最新 13F-HR 或 amendment 的 infotable XML 全量解析入库，
  变化量 = 最新季持仓 − 上季；披露有 45 天延迟（季度末 +45 天出现峰值）
- **网格扫描**：/backtest 页底部折叠区，按策略 spec 的 grid 参数组合一键扫描
  （例：ma200_panic 的 ma_trend=[100,150,200,250]，Sharpe 排序 Top20）
- IPO 事件：Nasdaq 月度日历，募资 ≥$500M 全收、≥$100M 科技/生物医药收

## TQQQ 合成数据（重要说明）

TQQQ 于 2010-02-11 上市。要回测 2000 年互联网泡沫，本平台用业界标准做法重建：

```
lev_t = 3 × QQQ日收益 − 2 × ^IRX融资成本/252 − 0.86%费用率/252
SYNTH_t = SYNTH_{t-1} × (1 + lev_t)      （QQQ 复权价 ≈ NDX 总收益）
拼接:    1999-03-10 → 2010-02-10 合成段 ‖ 2010-02-11 起真实 TQQQ（比率连续复利衔接）
```

`python cli.py verify-tqqq` 输出重叠期校验（合成 vs 真实日收益相关系数、年化漂移、终端净值比）。

> ⚠️ **2000-2002 极端尾部的可信度**：QQQ 峰谷 -83%，3 倍杠杆逐日复利数学上趋近归零。合成序列在该区间
> 只有「数量级」意义（压力测试视角），没有「精度」意义 —— 若当年真有 TQQQ，swap spread、
> 融资利率上浮、极端波动下的展期成本都会造成实质偏差。回测报告页会持续展示此声明。

## 自定义策略

`app/backtest/strategies/custom.py` 中复制一个类，改 `id/name/description/params`，实现
`build(prices, params) -> StrategyOutput(weights[, cashflows])`，加 `@register` 装饰器即完成注册，
前端策略下拉自动出现。引擎层统一 `shift(1)` 防前视，策略作者无需处理。

## 架构

```
app/data/        数据层: yfinance/FMP/FRED/Nasdaq/SEC loaders + Parquet 存储 + TQQQ 合成器
app/backtest/    回测: 向量化引擎(防前视/成本/DNA现金流) + 指标 + 策略注册表 + 网格扫描
app/market/      宽度/板块/宏观（Phase 2 填充）
app/events/      财报/IPO/事件日历（Phase 2 填充）
app/valuation/   估值（Phase 3 填充）
app/notify/      钉钉推送开关（NOTIFY_ENABLED 控制，默认关）
app/api/         FastAPI 路由
templates/       Jinja2 页面
```

- 行情存 Parquet（`data/daily/{TICKER}.parquet`，gzip），事件/指标/回测存 SQLite（WAL）
- `.env` 管理密钥（绝不入库入 git）；回测功能零 Key 即可用
- APScheduler 嵌入 uvicorn：每日自动增量同步行情

## 数据源（全免费，按环境自动降级）

fetch 链按标的类型自動切换（`app/data/loaders/yfinance_loader.py:fetch_single`）：

| 数据类型 | 主源 | 备源1 | 备源2 |
|---|---|---|---|
| 美股/ETF 日线 | yfinance（复权准） | 腾讯财经（1999起,qfq） | 东方财富（2001起,fqt=1）→ Stooq |
| BTC 日线 | yfinance（BTC-USD 2014起） | 币安公开归档（2017-08起） | CoinGecko |
| 利率 ^TNX/^TYX/^IRX | 美国财政部官方曲线（大陆可达,1990起全史） | FRED | yfinance |
| 财报/IPO 日历（Phase 2） | FMP 免费档 250/天 | Nasdaq API | — |
| 机构持仓（Phase 4） | SEC EDGAR | — | — |

- **大陆直连环境**：Yahoo 403、FRED/OKX/CoinGecko 超时的情况下，由腾讯/东财/币安归档 保证全链路可用
- 东财美股只从 2001 起，QQQ 的 1999-2000 段（含 2000 年 3 月泡沫顶 **118.47**）由腾讯窗口分段请求补齐
- **无假数据铁律**：利率域已下线分段常数兜底（2026-10-10）——真源全部不可达时该数据
  直接不显示（对合成 TQQQ 的影响见 verify-tqqq 月度对账），绝不显示近似值冒充真数据
- 挂代理环境将自动回到 yfinance 主链（数据复权精度最高）；`python cli.py refresh` 可在任何时候强制全量重下

## 免责声明

仅供个人研究学习，不构成投资建议。数据来自公开免费渠道，可能存在错漏。

## 参考项目(实现复杂功能时对照)

| 项目 | 参考价值 |
|---|---|
| [davidwang0116/Quant-QQQ-QLD-TQQQ-SMA200Timing-MixedPosition-Backtest](https://github.com/davidwang0116/Quant-QQQ-QLD-TQQQ-SMA200Timing-MixedPosition-Backtest) | leveraged_mix 策略(60-30-10 + SMA200 择时)的原始出处（该策略随 REGISTRY 精简已删除） |
| [Totoro-qaq/stocks-analysis](https://github.com/Totoro-qaq/stocks-analysis) | 股票分析视角/指标组织的参考 |
| [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis) | 每日消息/日报类看板的内容组织参考 |
| [star23/day1global-briefing](https://github.com/star23/day1global-briefing) | 全球宏观简报(简报结构与选题粒度参考) |
| [tepeiya/stock-heatmap](https://github.com/tepeiya/stock-heatmap) | 美股总览页的 treemap 热力图形式参考(另见 coinglass.com/stock-heatmap) |
