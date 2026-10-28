# 结构代码来源与计算口径

本工具按用户要求从 [xiaodandandandan/Stock_workDashboard](https://github.com/xiaodandandandan/Stock_workDashboard) 抽取缠论结构函数。

- 上游版本：`4aacef693c87e9cab857ef23b11089dfd49cc64d`。
- 直接来源文件：`core/_engine/chanlun_core.py`。
- 来源文件 SHA-256：`61679c24c3af70cab9cc9d7d231fa9b68e4be654f2323f98b98098013fcda774`。
- 抽取函数：`merge_bars`、`find_fenxing`、`find_bi`、`find_zhongshu`，及常量 `MIN_GAP=4`。
- 算法保持原样；本工具增加 `calculate_structure` 封装、输入检查、逐笔字段和尾部暂定标记。删除买卖点、背驰、MACD、信号回放和通达信文件读取代码。
- 后续按用户要求在独立 `indicators.py` 中增加 MACD (12,26,9) 供图表展示，EMA 初始值和运算顺序与上游保持一致；它不参与笔、中枢或均线筛选判断。
- 原文件说明其抽取自 `chanlun_scan.py`；强调合并 K 线必须携带原始索引，`mi` 用于笔的合并 K 线间距，`i` 用于原始 K 线绘图位置。本工具保留这项规则。

上游的 `core/_engine/NOTICE.md` 记载：该文件来自本机 `~/.workbuddy/skills/chanlun-kline/scripts/chanlun_core.py`，技能包元数据版本为 1.0.3、声明 MIT；原始文件未提供可核验的原作者或上游仓库地址。随附 `LICENSE.chanlun-kline` 保留上游提供的许可文本。本说明仅陈述该核心文件的已有许可说明，不代表整个来源仓库采用 MIT。

## 必须了解的结构口径

1. 笔采用上游的分型筛选算法，默认要求两个端点的合并 K 线序号间距至少为 4；同型分型取更极端者，顶价必须高于底价。
2. 中枢按每连续三笔独立计算 `ZD=max(三笔低点)`、`ZG=min(三笔高点)`，严格要求 `ZD<ZG`。相邻重叠窗口可能分别出现；本工具没有中枢延伸合并、线段分解或中枢级别升级。
3. 最新笔的端点会随后续分型变化。本工具将最后一段笔及包含它的三笔中枢标为 `provisional=true`，图表应以暂定样式展示。`false` 仅表示不是当前尾段，不是交易确认或结构永久不变的保证；全量重算、历史数据修订与前端截断仍可能改变结构。
4. 未满足端点条件时，`points` 可能只有一个候选点，`strokes` 仍为空；“点数”和“笔数”不可混用。
5. 仅计算结构，不识别任何类别买卖点，不判断背驰，不执行交易。

`tests/test_chan.py` 提供自包含边界测试。在存在上游源码时还可运行固定样本和随机有效 OHLC 的逐字段一致性验证；交付包不含完整上游源码，可通过环境变量 `CHAN_UPSTREAM_PATH` 指定该文件。
