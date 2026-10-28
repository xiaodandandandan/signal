# 日 K 均线 + 缠论笔 / 中枢扫描器

从用户指定的 [Stock_workDashboard](https://github.com/xiaodandandandan/Stock_workDashboard) 提取结构函数，做成独立、零第三方依赖的 Python 工具。来源版本为 `4aacef693c87e9cab857ef23b11089dfd49cc64d`。只计算包含、分型、笔和中枢，以及下述均线条件。

## 筛选条件

默认扫描沪、深、北交易所在市 A 股的**日 K**，包含 ST，不包含 ETF、B 股、港股和美股。每只股票只获取并计算最近 **120 根日 K**（数据不足时使用实际可用数量），以下四个条件必须同时成立：

1. **短线条件满足以下任一项**：最近完成的日 K 出现 `MA5 上穿 MA10`；或当前 `abs(MA5−MA10) <= 1元`，允许 MA5 暂时略低于 MA10。
2. **其余均线保持多头排列**：`MA10 > MA20 > MA60`。原金叉分支仍要求 `MA5 > MA10`；“均线接近”分支放宽 MA5 / MA10 的先后关系。
3. **MA60 向上**：默认最近 3 次相邻变化均严格上升，即 `MA60[t] > MA60[t-1] > MA60[t-2] > MA60[t-3]`。至少需要 63 根日 K。
4. **MA5、MA20 向上**：最新值均严格高于前一根，斜率（元/根）大于零；走平或下降均排除，两种短线分支都适用。

MA 为收盘价的简单移动平均，比较前不做显示取整；`1e-12` 以内的绝对/相对浮点误差视为相等。差值阈值是**价格绝对差 1 元，包含边界**，不是百分比；不要求“均线接近”类型已发生金叉。原有金叉结果即使差值超过 1 元也会保留。两类在列表和 CSV 中分别标为“金叉”“均线接近”，并提供有符号及绝对差值。

`--cross-lookback N` 可以把金叉分支放宽成最近 N 根发生金叉，该分支当前仍需完整多头排列。`--near-ma-gap 1.0` 可调整接近阈值，`--strict-cross` 关闭接近分支，恢复原来仅金叉的模式。`--trend-bars 1` 可改成仅比较 MA60 当前值与前一根。

默认按北京时间 15:00 排除当天未完成日 K，并始终排除未来日期；这是时间门槛，不保证行情商已最终结算。停牌等造成的陈旧截面不进入当天命中清单。数据日期参考上证指数与本批股票最新日期；参考指数失败会在报告警告。日 K 沿用原仓库新浪接口，复权口径标记为 `provider_default`，不宣称前复权；不混用不同来源或复权序列。

## 运行

需要 Python 3.10+，不需要 `pip install`。克隆仓库后运行：

```shell
git clone https://github.com/xiaodandandandan/signal.git
cd signal
```

在本目录执行：

```powershell
# 打开浅色扫描页，在右上角点击“重新扫描”
python dashboard.py

# 扫描沪深北全市场，并打开结果
python scan.py --open

# 或用 Python Launcher
py -3 scan.py --open

# 先扫前 100 只测试行情连接（报告会标为限定数量）
python scan.py --limit 100 --workers 4

# 最近 3 根内金叉，MA60 只要求最近一次上升
python scan.py --cross-lookback 3 --trend-bars 1

# 只保留原来严格的金叉结果
python scan.py --strict-cross

# 只分析指定股票；带交易所前缀更明确
python scan.py --codes sh600000,sz000001,bj920118

# 离线演示：所有价格为合成数据，报告显著标注，不代表市场结果
python scan.py --demo --output-dir demo_results --open

# 参数列表
python scan.py --help
```

Windows 双击 `run_scan.bat` 会打开浅色扫描页。点击右上角 **重新扫描** 开始扫描，页面显示进度，完成后自动打开新结果；扫描中按钮会禁用，失败时保留已有报告。保持启动窗口打开即可随时重扫；不创建定时任务。启动器优先使用本目录虚拟环境或电脑上已有的 Codex Python 运行时，也可使用系统 Python。

主图下方显示 **MACD (12,26,9)**，包含 DIF、DEA 和红绿柱。MACD 只用于看图，不增加筛选条件。**双击 K 线或 MACD 副图**，或点击“放大查看”，会打开大图弹窗；支持放大、缩小、重置 120 根、顶部前后按钮平移和 Esc 关闭。主图、MACD 和弹窗始终使用相同的日期坐标。

页面重新扫描固定使用最近 120 根，并重置试扫数量限制为全市场。历史批次保留当时的实际取数根数，新扫描按 120 根重算笔与中枢。命令行 `--count` 仍可供需要其他历史长度的高级调用使用。

默认地址为 `http://127.0.0.1:8765/`，仅本机可访问。可用 `python dashboard.py --port 8766` 指定其他端口。已有 `report.html` 仍可双击离线查看；离线报告的重新扫描按钮会连接本机默认端口的扫描服务，服务未启动时会提示先双击 `run_scan.bat`。`python dashboard.py --demo --output-dir demo_results` 可用合成行情验证按钮流程，页面会标明演示数据。

## 输出

每次成功扫描保存在 `results/年月日-时分秒/`：

| 文件 | 内容 |
| --- | --- |
| `report.html` | 浅色独立报告；日 K、均线、笔、中枢、MACD，双击放大弹窗；离线可看，连接本机服务后可重新扫描 |
| `matches.csv` | 命中股票、金叉 / 均线接近类型、MA5−MA10 差值、均线实测值、金叉日期及结构数量，UTF-8 BOM 便于 Excel 打开 |
| `result.json` | 命中股票本次实际 K 线、均线和 MACD 序列、结构、筛选明细、来源、日期、覆盖率、失败明细 |
| `structures.jsonl` | **所有成功分析股票**的笔 / 中枢和均线判定，含未命中股票；每行一只，不保存全量 OHLC |

`results/latest.json` 指向最近成功批次，`results/attempt.json` 记录最近扫描诊断。市场列表缺页、重复、为空或未覆盖沪深北时拒绝标为全市场；单票取数 / 计算成功率低于 95% 时不发布新批次，保留原 `latest.json`。成功率达到 95% 且仍有错误时，报告 `complete=false` 并展示失败明细。K 线历史不足、无均线命中、旧日期分别统计，不当成连接失败。

图表只展示均线命中的股票，结构在本次最多 120 根日 K 上计算；缩放仅改变显示区间。每只成功取数的股票都会计算笔和中枢，未命中者可从 `structures.jsonl` 读取。MACD 以窗口首个收盘初始化 EMA，因此缩短历史后，其数值可能与长历史平台略有差异；柱为 `2 × (DIF − DEA)`，前 35 根视为指标预热区间。

## 核心代码

```text
chan_scanner/
  chan.py        原仓库的包含 / 分型 / 笔 / 三笔重叠中枢
  signals.py     MA金叉或近距、多头排列、MA5/MA20斜率、MA60向上
  indicators.py  MACD (12,26,9)，仅用于图表
  market.py      沪深北市场列表、日K、完成K与数据校验
  scanner.py     并发扫描、旧日期排除、覆盖率与失败明细
  report.py      独立HTML、CSV、JSON输出
  cli.py         命令行与离线数据入口
  server.py      本机扫描页面、后台任务与进度接口
```

直接在代码中调用：

```python
from chan_scanner.chan import calculate_structure
from chan_scanner.market import validate_bars, completed_daily_bars
from chan_scanner.signals import evaluate

# bars 为按日期升序的 [{date, open, high, low, close, volume}, ...]
bars = validate_bars(bars)
bars, dropped = completed_daily_bars(bars)
structure = calculate_structure(bars)
condition = evaluate(bars, cross_lookback=1, trend_bars=3)

print(structure["strokes"])  # start/end 为原始 K 线的零基索引
print(structure["hubs"])     # i0/i1/k/ZD/ZG/provisional
print(condition["matched"], condition["ma"], condition["reasons"])
```

笔默认采用上游 `MIN_GAP=4`，用合并 K 线序号判断间距，绘图位置保留原始 K 线索引。中枢是**每连续三笔的严格重叠区间**：`ZD=max(各笔低点)`、`ZG=min(各笔高点)`，要求 `ZD<ZG`。保留上游滚动窗口口径，多个中枢窗口可以重叠；没有中枢合并、线段分解或级别升级。

最后一段笔和依赖它的中枢带 `provisional=true`，图上用虚线 / 虚框表示，后续日 K 可能改变端点。`false` 仅表示非当前尾段，并非结构永远不变的保证。代码不包含买卖点、背驰或交易功能，MACD 独立计算用于展示。详细来源和结构局限见 `NOTICE.md`，核心许可文本见 `LICENSE.chanlun-kline`。

## 离线输入与测试

可以从已有行情文件计算，避免再次联网：

```powershell
python scan.py --input bars.json --output-dir offline_results
python -B -m unittest discover -s tests -v
```

`bars.json` 为数组，每项包含 `symbol`、`name`、`bars`。日期可为 `20261027` 或 `"2026-10-27"`；输入会排序，重复日期、非有限数、价格高低错误、负成交量会拒绝。

测试包括结构边界、金叉与趋势精度、市场分页完整性、陈旧截面、完成日 K、失败比例、离线输入和报告输出。若另有原仓库源码，可设置 `CHAN_UPSTREAM_PATH` 为其 `core/_engine/chanlun_core.py` 路径，执行额外的算法逐字段一致性测试；交付包没有复制完整原仓库。

新增共同条件：MA5 和 MA20 最新一根相对前一根的差值均须严格大于零（元/根），走平或下降均排除；金叉与均线接近两分支都必须满足。
