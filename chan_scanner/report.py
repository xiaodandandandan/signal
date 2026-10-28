"""Light-theme reports, local rescan controls, and CSV/JSON output."""

import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile

from .indicators import macd


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".writing-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_text(payload):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["股票代码", "名称", "日K日期", "收盘", "MA5", "MA10", "MA20", "MA60",
                     "金叉日期", "距今根数", "入选类型", "MA5减MA10", "两线绝对差(元)",
                     "MA60连续上升次数", "笔数", "中枢窗口数", "末笔可变"])
    for row in payload["rows"]:
        cond, structure = row["condition"], row["structure"]
        writer.writerow([_csv_cell(x) for x in [row["symbol"], row["name"], row["date"], row["close"],
                         *[cond["ma"].get(str(p)) for p in (5, 10, 20, 60)],
                         cond["cross"].get("date"), cond["cross"].get("bars_ago"),
                         cond.get("match_label") or "金叉", cond.get("ma5_ma10_gap"),
                         cond.get("ma5_ma10_gap_abs"),
                         cond["trend_bars"], len(structure["strokes"]), len(structure["hubs"]),
                         bool(structure["strokes"] and structure["strokes"][-1]["provisional"])]])
    return "\ufeff" + stream.getvalue()


def html_text(payload):
    # Escape data before embedding it in an HTML script element. Text fields are
    # assigned through textContent in the viewer, never interpolated as markup.
    visible = {k: v for k, v in payload.items() if k != "records"}
    # Historical reports can acquire the new display without altering their
    # saved scan inputs or their original result JSON.
    visible["rows"] = [{**row, "macd": row.get("macd") or macd(row["bars"])}
                       for row in payload["rows"]]
    data = json.dumps(visible, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return _HTML.replace("__SCAN_DATA__", data)


def save_result(payload, output_dir):
    """Publish an immutable run first, then atomically update the latest pointer."""
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    run = root / timestamp
    suffix = 0
    while run.exists():
        suffix += 1
        run = root / f"{timestamp}-{suffix:03d}"
    run.mkdir()
    summary = {k: v for k, v in payload.items() if k != "records"}
    atomic_text(run / "result.json", json_text(summary))
    atomic_text(run / "matches.csv", csv_text(payload))
    atomic_text(run / "structures.jsonl", "".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in payload["records"]))
    atomic_text(run / "report.html", html_text(payload))
    latest = {"generated": payload["generated"], "market_date": payload["market_date"],
              "scope": payload["scope"], "hits": payload["hits"],
              "complete": payload["complete"], "run": run.name,
              "report": f"{run.name}/report.html", "result": f"{run.name}/result.json"}
    atomic_text(root / "latest.json", json_text(latest))
    atomic_text(root / "attempt.json", json_text({"status": "published", **latest,
                                                "errors": payload["errors"]}))
    return run


_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>日 K 均线与缠论结构扫描</title>
<style>
:root{color-scheme:light;--bg:#f4f7fb;--panel:#fff;--line:#dfe6ef;--dim:#64748b;--text:#172c48;--green:#087f73;--orange:#a76210}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:28px}
header{display:flex;align-items:start;justify-content:space-between;gap:24px}h1{font-size:26px;margin:0 0 4px;font-weight:650}p{margin:6px 0;color:var(--dim)}.tag{color:var(--green);font-size:12px;letter-spacing:2px}
.header-actions{display:flex;align-items:flex-end;flex-direction:column;gap:10px}.header-actions p{white-space:pre-line;text-align:right;font-size:12px}.primary{background:#2563eb;color:#fff;border-color:#2563eb;padding:9px 18px;font-weight:600;box-shadow:0 3px 10px #2563eb20}.primary:hover{background:#1d4ed8;border-color:#1d4ed8}.primary:disabled{cursor:wait;opacity:.65}
.scan-status{display:flex;align-items:center;gap:16px;margin:18px 0 0;background:#edf4ff;border:1px solid #d6e5fc;border-radius:10px;padding:12px 16px;color:#31577f}.scan-status[hidden]{display:none}.scan-status progress{width:180px;max-width:35%;accent-color:#2563eb}.scan-status.error{background:#fff7ed;border-color:#f3d4ab;color:#975b13}.scan-status small{display:block;color:inherit}.scan-status a{color:#1d4ed8}.scan-status code{background:#ffffffb0;padding:1px 5px;border-radius:4px}.scan-status [hidden]{display:none}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:24px 0}.stat,.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 3px 15px #1e3a5f04}.stat{padding:14px 18px;color:var(--dim)}.stat strong{display:block;font-size:28px;color:var(--text);font-weight:600}.layout{display:grid;grid-template-columns:310px 1fr;gap:16px}.panel{padding:18px;min-width:0}
input,select,button{font:inherit;color:var(--text);background:#fff;border:1px solid #ccd7e5;border-radius:7px;padding:7px 10px}input{width:100%}input:focus,select:focus,button:focus-visible{outline:2px solid #93b4f5;outline-offset:2px}button{cursor:pointer}button:hover{border-color:var(--green)}.rows{margin-top:12px;max-height:660px;overflow:auto}.row{display:block;width:100%;text-align:left;margin:6px 0;background:transparent;border-color:var(--line);padding:10px 12px}.row.active{border-color:#39a798;background:#edf9f5}.row small{display:block;color:var(--dim)}
.topline{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.topline strong{font-size:18px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}label{cursor:pointer;color:var(--dim)}label input{width:auto;accent-color:#2563eb}canvas{display:block;width:100%;height:440px;background:#fcfdff;border:1px solid #edf1f6;border-radius:8px;touch-action:pan-y}.legend{display:flex;gap:15px;flex-wrap:wrap;font-size:12px;color:var(--dim);margin:10px 0}.legend b{font-weight:normal}.detail{font-variant-numeric:tabular-nums}.notice{padding:10px 14px;border-left:3px solid var(--orange);margin:12px 0;background:#fff5e5;color:#8a591a}.empty{padding:60px 20px;text-align:center;color:var(--dim)}footer{margin-top:20px;font-size:12px;color:var(--dim)}details{margin-top:12px;color:var(--dim)}pre{white-space:pre-wrap;word-break:break-all;font-size:12px}#hover{min-height:24px;color:var(--dim);font-size:12px}
@media(max-width:900px){main{padding:16px}.layout{grid-template-columns:1fr}.stats{grid-template-columns:repeat(3,1fr)}.rows{max-height:180px}canvas{height:370px}header{display:block}.header-actions{align-items:flex-start;margin-top:12px}.header-actions p{text-align:left}h1{font-size:22px}.scan-status{align-items:start;flex-direction:column}.scan-status progress{max-width:100%;width:100%}}@media print{body{background:#fff;color:#000}.panel,.stat{border-color:#aaa}button,input,.toolbar,.scan-status{display:none}}

.match-badge{display:inline-block;font-size:11px;font-weight:500;line-height:1.7;border-radius:4px;padding:0 5px;margin-left:7px;vertical-align:middle;background:#eaf3ff;color:#245fa6}.match-badge.near{background:#fff2d9;color:#8e5c14}.list-summary{font-size:12px;color:var(--dim);padding:5px 1px}.load-more{display:block;width:100%;margin:12px 0;background:#f6f9fd}.stat small{display:block;font-size:11px;margin-top:3px;color:var(--dim)}.chart-hint{font-size:12px;color:var(--dim);align-self:center}.price-chart{height:360px;cursor:crosshair}.macd-chart{height:150px;cursor:crosshair}.macd-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:13px 0 5px;color:var(--dim);font-size:12px}.macd-heading strong{font-weight:600}.macd-heading span{display:flex;gap:14px}.macd-heading b{font-weight:500}.dif-label{color:#b86b08}.dea-label{color:#2563eb}.hist-label{color:#b4414e}.chart-hover{min-height:24px;color:var(--dim);font-size:12px;font-variant-numeric:tabular-nums}
#chart-dialog{width:calc(100vw - 36px);max-width:1640px;max-height:calc(100vh - 30px);padding:20px 24px;border:1px solid var(--line);border-radius:16px;background:#fff;color:var(--text);box-shadow:0 24px 100px #1a345e35;overflow:auto}#chart-dialog::backdrop{background:#172c4870;backdrop-filter:blur(3px)}.dialog-heading{display:flex;justify-content:space-between;gap:15px;align-items:start}.dialog-heading strong{font-size:20px}.dialog-heading p{font-size:12px;margin:3px 0}.dialog-toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:12px 0}.dialog-toolbar small{color:var(--dim);margin-left:auto}.dialog-toolbar span{color:var(--dim);font-size:12px}#dialog-chart{height:clamp(220px,calc(100vh - 380px),650px)}#dialog-macd{height:140px}#zoom-in:disabled,#zoom-out:disabled{opacity:.45;cursor:default}body:has(#chart-dialog[open]){overflow:hidden}
@media(max-width:900px){.price-chart{height:300px}.macd-chart{height:135px}#chart-dialog{padding:14px;width:calc(100vw - 12px);max-height:calc(100vh - 12px)}.dialog-toolbar small{width:100%;margin:0}.dialog-heading strong{font-size:17px}#dialog-chart{height:clamp(220px,calc(100vh - 390px),500px)}}
</style></head><body><main><header><div><div class="tag">CHAN / DAILY SCANNER</div><h1>日 K 均线与缠论结构扫描</h1><p id="rule"></p></div><div class="header-actions"><button type="button" id="rescan" class="primary">重新扫描</button><p id="meta"></p></div></header>
<section id="scan-status" class="scan-status" role="status" aria-live="polite" hidden><progress id="scan-progress" hidden aria-label="扫描进度"></progress><div><span id="scan-message"></span><small id="service-help" hidden>请先双击 <code>run_scan.bat</code>，再<a id="dashboard-link" href="http://127.0.0.1:8765/">打开扫描页</a>。</small></div></section>
<div id="warnings"></div><div class="stats" id="stats"></div><div class="layout"><aside class="panel"><strong>符合条件的股票</strong><p>点击个股核对均线、笔和三笔重叠中枢</p><input id="search" placeholder="搜索代码 / 名称" aria-label="搜索股票"><div class="rows" id="rows"></div></aside><section class="panel"><div class="topline"><strong id="title">选择股票</strong><span class="detail" id="numbers"></span></div><div class="toolbar"><select id="window" aria-label="显示K线根数"><option value="120" selected>最近120根</option><option value="90">最近90根</option><option value="60">最近60根</option><option value="30">最近30根</option></select><button type="button" id="pan-back" aria-label="查看更早K线">← 前段</button><button type="button" id="pan-forward" aria-label="查看更新K线">后段 →</button><label><input type="checkbox" id="showMA" checked> 均线</label><label><input type="checkbox" id="showBi" checked> 笔</label><label><input type="checkbox" id="showHub" checked> 中枢</label><button type="button" id="expand-chart">放大查看</button><span class="chart-hint">双击图表放大</span></div><div class="legend"><b style="color:#aa7410">MA5</b><b style="color:#2563eb">MA10</b><b style="color:#8755bb">MA20</b><b style="color:#475569">MA60</b><b style="color:#008b78">笔 · 末笔虚线</b><b style="color:#b47712">中枢 · 可变末段虚框</b></div><canvas id="chart" class="price-chart" aria-label="日K、均线、笔与中枢图"></canvas>
<div class="macd-heading"><strong>MACD (12, 26, 9)</strong><span><b class="dif-label">DIF</b><b class="dea-label">DEA</b><b class="hist-label">红绿柱 ×2</b></span></div>
<canvas id="macd-chart" class="macd-chart" aria-label="MACD副图"></canvas><div id="hover"></div><p id="condition"></p><p id="structure"></p><details><summary>数据来源与口径</summary><pre id="source"></pre></details></section></div><footer>中枢沿用原仓库的连续三笔重叠窗口，允许多个窗口重叠，不做中枢合并或级别升级。末笔及依赖末笔的中枢仍可随新 K 线变化。本报告只计算结构与均线条件。</footer></main>
<dialog id="chart-dialog" aria-labelledby="dialog-title">
  <div class="dialog-heading"><div><strong id="dialog-title">K 线放大</strong><p id="dialog-meta"></p></div><button type="button" id="close-dialog" aria-label="关闭放大图">关闭 ×</button></div>
  <div class="dialog-toolbar"><button type="button" id="zoom-in" aria-label="放大K线">放大 ＋</button><button type="button" id="zoom-out" aria-label="缩小K线">缩小 −</button><button type="button" id="zoom-reset">重置120根</button><button type="button" id="dialog-pan-back" aria-label="放大图查看更早K线">← 前段</button><button type="button" id="dialog-pan-forward" aria-label="放大图查看更新K线">后段 →</button><span id="dialog-range"></span><small>上方按钮切换区间 · Esc 关闭</small></div>
  <canvas id="dialog-chart" class="price-chart" aria-label="放大日K与笔中枢图"></canvas>
  <div class="macd-heading"><strong>MACD (12, 26, 9)</strong><span><b class="dif-label">DIF</b><b class="dea-label">DEA</b><b class="hist-label">红绿柱 ×2</b></span></div>
  <canvas id="dialog-macd" class="macd-chart" aria-label="放大MACD副图"></canvas>

  <div id="dialog-hover" class="chart-hover"></div>
</dialog>
<script type="application/json" id="scan-data">__SCAN_DATA__</script><script>
"use strict";
const data=JSON.parse(document.getElementById('scan-data').textContent), $=id=>document.getElementById(id), colors={5:'#aa7410',10:'#2563eb',20:'#8755bb',60:'#475569'}, extras=['#c64e54','#54852e','#a44e89'];
let selected=null,coords=null,dialogCoords=null,dialogCount=120,dialogOffset=0,viewOffset=0,listLimit=200;
const date=x=>x==null?'待扫描':String(x).replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3');
const n=x=>x==null?'—':Number(x).toFixed(3);
const maOrder=data.settings.bullish_periods.map(x=>'MA'+x).join(' > ');
const legend=document.querySelector('.legend');legend.replaceChildren();data.settings.bullish_periods.forEach((p,i)=>{const e=document.createElement('b');e.style.color=colors[p]||extras[i%extras.length];e.textContent='MA'+p;legend.append(e)});for(const [text,color] of [['笔 · 末笔虚线','#008b78'],['中枢 · 可变末段虚框','#b47712']]){const e=document.createElement('b');e.style.color=color;e.textContent=text;legend.append(e)}
const nearThreshold=data.settings.near_ma_gap;
const nearEnabled=typeof nearThreshold==='number'&&Number.isFinite(nearThreshold);
const lowerOrder=data.settings.bullish_periods.filter(p=>p!==5).map(p=>'MA'+p).join(' > ');
$('rule').textContent=nearEnabled?`MA5 上穿 MA10，或 |MA5−MA10| ≤ ${nearThreshold} 元 · ${lowerOrder} · MA5、MA20 最新斜率 > 0 · MA60 连续 ${data.settings.trend_bars} 次上升`:`MA5 上穿 MA10（最近 ${data.settings.cross_lookback} 根） · ${maOrder} · MA5、MA20 最新斜率 > 0 · MA60 连续 ${data.settings.trend_bars} 次上升`;
function selectionLabel(row){return row.condition.match_label||'金叉'}
function selectionDescription(row){
  const c=row.condition;
  return c.match_type==='near'?`均线接近 · 差 ${n(c.ma5_ma10_gap_abs)} 元（≤${c.near_ma_gap}元）`:`金叉 ${date(c.cross.date)}`;
}
$('meta').textContent=`数据日期 ${date(data.market_date)}\n生成时间 ${data.generated}`;
for(const warning of data.warnings){const e=document.createElement('div');e.className='notice';e.textContent=warning;$('warnings').append(e)}
if(data.source.provider==='synthetic_demo'){const e=document.createElement('div');e.className='notice';e.textContent='离线演示：所有行情均为构造数据，不代表真实市场扫描。';$('warnings').prepend(e)}
const scopeName={all_a_shares:'沪深北全市场',limited_market:'限定数量试扫',specified_symbols:'指定股票 / 离线数据'}[data.scope]||data.scope;
for(const [label,value] of [[scopeName,data.requested],['成功分析',data.success],['均线命中',data.hits],['陈旧截面排除',data.stale],['失败 / 历史不足',`${data.errors.length} / ${data.insufficient}`]]){const e=document.createElement('div');e.className='stat';e.textContent=label;const v=document.createElement('strong');v.textContent=value;e.append(v);if(label==='均线命中'&&nearEnabled){const small=document.createElement('small'),nearCount=data.rows.filter(r=>r.condition.match_type==='near').length;small.textContent=`金叉 ${data.hits-nearCount} · 均线接近 ${nearCount}`;e.append(small)}$('stats').append(e)}
$('source').textContent=JSON.stringify({scope:data.scope,source:data.source,settings:data.settings,reference_date:data.reference_date,reference_method:data.reference_method,complete:data.complete,errors:data.errors},null,2);
function renderList(){
  const q=$('search').value.toLowerCase(),filtered=data.rows.filter(r=>(r.symbol+r.name).toLowerCase().includes(q));
  $('rows').replaceChildren();
  if(filtered.length>200){const summary=document.createElement('div');summary.className='list-summary';summary.textContent=`显示 ${Math.min(listLimit,filtered.length)} / ${filtered.length} 只，可搜索全部结果`;$('rows').append(summary)}
  for(const row of filtered.slice(0,listLimit)){
    const e=document.createElement('button');e.className='row'+(selected===row?' active':'');e.textContent=row.name+' · '+row.symbol;
    const badge=document.createElement('span');badge.className='match-badge'+(row.condition.match_type==='near'?' near':'');badge.textContent=selectionLabel(row);e.append(badge);
    const sm=document.createElement('small');sm.textContent=`收盘 ${n(row.close)} · ${selectionDescription(row)}`;e.append(sm);
    e.onclick=()=>{selected=row;viewOffset=0;renderList();render()};$('rows').append(e);
  }
  if(filtered.length>listLimit){const more=document.createElement('button');more.className='load-more';more.textContent=`再显示 ${Math.min(200,filtered.length-listLimit)} 只`;more.onclick=()=>{listLimit+=200;renderList()};$('rows').append(more)}
  if(!filtered.length){const e=document.createElement('p');e.className='empty';e.textContent=data.reference_method==='not_scanned'?'尚未扫描，点击右上角重新扫描':(data.hits?'没有匹配的搜索结果':'本批次没有满足全部条件的股票');$('rows').append(e)}
}

function canvasContext(canvas){
  const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1;
  canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);
  const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.clearRect(0,0,rect.width,rect.height);
  return {ctx,w:rect.width,h:rect.height};
}
function chartRange(total,count,offset){
  count=Math.min(total,Math.max(1,count));offset=Math.max(0,Math.min(offset,total-count));
  const end=total-offset;return {start:end-count,end,count,offset};
}
function drawPrice(row,canvas,range){
  const {ctx,w,h}=canvasContext(canvas);
  if(!row){ctx.fillStyle='#64748b';ctx.font='13px system-ui';ctx.fillText(data.reference_method==='not_scanned'?'点击右上角重新扫描，完成后查看 K 线、笔和中枢。':'本批次无命中，请核对筛选条件与失败明细。',18,h/2);return null}
  const {start,end}=range,bars=row.bars,view=bars.slice(start,end),left=58,right=w-16,top=17,bottom=h-28,step=(right-left)/Math.max(1,view.length);
  let lo=Math.min(...view.map(b=>b.low)),hi=Math.max(...view.map(b=>b.high));
  if($('showMA').checked)for(const values of Object.values(row.ma_series))for(const v of values.slice(start,end))if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v)}
  if($('showHub').checked)for(const z of row.structure.hubs)if(z.i1>=start&&z.i0<end){lo=Math.min(lo,z.ZD);hi=Math.max(hi,z.ZG)}
  const span=hi-lo||1;lo-=span*.07;hi+=span*.07;
  const x=i=>left+(i-start+.5)*step,y=p=>bottom-(p-lo)/(hi-lo)*(bottom-top);
  ctx.font='11px system-ui';
  for(let k=0;k<5;k++){const price=lo+(hi-lo)*k/4,yy=y(price);ctx.strokeStyle='#e2e8f0';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(right,yy);ctx.stroke();ctx.fillStyle='#64748b';ctx.fillText(price.toFixed(2),4,yy+4)}
  ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
  if($('showHub').checked){for(const z of row.structure.hubs){if(z.i1<start||z.i0>=end)continue;const xx=Math.max(left,x(z.i0)),xe=Math.min(right,x(z.i1));ctx.fillStyle='rgba(184,119,16,.08)';ctx.strokeStyle='rgba(184,119,16,.65)';ctx.setLineDash(z.provisional?[5,4]:[]);ctx.fillRect(xx,y(z.ZG),xe-xx,y(z.ZD)-y(z.ZG));ctx.strokeRect(xx,y(z.ZG),xe-xx,y(z.ZD)-y(z.ZG))}ctx.setLineDash([])}
  const bw=Math.max(1,Math.min(step*.65,12));
  view.forEach((b,j)=>{const xx=x(start+j);ctx.strokeStyle=ctx.fillStyle=b.close>=b.open?'#d34853':'#168568';ctx.beginPath();ctx.moveTo(xx,y(b.high));ctx.lineTo(xx,y(b.low));ctx.stroke();ctx.fillRect(xx-bw/2,Math.min(y(b.open),y(b.close)),bw,Math.max(1,Math.abs(y(b.open)-y(b.close))))});
  if($('showMA').checked)Object.entries(row.ma_series).forEach(([period,values],k)=>{ctx.strokeStyle=colors[period]||extras[k%extras.length];ctx.lineWidth=1.1;ctx.beginPath();let begun=false;for(let i=start;i<end;i++){if(values[i]==null){begun=false;continue}if(!begun){ctx.moveTo(x(i),y(values[i]));begun=true}else ctx.lineTo(x(i),y(values[i]))}ctx.stroke()});
  if($('showBi').checked){for(const s of row.structure.strokes){if(s.end<start||s.start>=end)continue;ctx.strokeStyle='#008b78';ctx.lineWidth=1.7;ctx.setLineDash(s.provisional?[6,4]:[]);ctx.beginPath();ctx.moveTo(x(s.start),y(s.start_price));ctx.lineTo(x(s.end),y(s.end_price));ctx.stroke()}ctx.setLineDash([]);ctx.lineWidth=1}
  ctx.restore();ctx.fillStyle='#64748b';
  for(let k=0;k<4;k++){const i=start+Math.round((view.length-1)*k/3);ctx.fillText(date(bars[i].date),Math.max(left,Math.min(right-72,x(i)-30)),h-7)}
  canvas.dataset.start=String(start);canvas.dataset.end=String(end);
  return {left,right,start,end,step,bars,row};
}
function drawMacd(row,canvas,range){
  const {ctx,w,h}=canvasContext(canvas);if(!row)return;
  const indicator=row.macd;if(!indicator){ctx.fillStyle='#64748b';ctx.font='12px system-ui';ctx.fillText('重新扫描后显示 MACD',60,h/2);return}
  const {start,end}=range,left=58,right=w-16,top=12,bottom=h-16,step=(right-left)/Math.max(1,end-start);
  const values=[...indicator.dif.slice(start,end),...indicator.dea.slice(start,end),...indicator.histogram.slice(start,end)];
  const extent=Math.max(0.0001,...values.map(v=>Math.abs(v)))*1.13;
  const x=i=>left+(i-start+.5)*step,y=v=>top+(extent-v)/(extent*2)*(bottom-top);
  ctx.font='10px system-ui';for(const value of [-extent,0,extent]){const yy=y(value);ctx.strokeStyle=value===0?'#b9c6d7':'#e7edf4';ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(right,yy);ctx.stroke();ctx.fillStyle='#64748b';ctx.fillText(value.toFixed(3),3,yy+3)}
  const bw=Math.max(1,Math.min(step*.6,12));
  for(let i=start;i<end;i++){const value=indicator.histogram[i];ctx.fillStyle=value>=0?'#d34853':'#168568';ctx.fillRect(x(i)-bw/2,Math.min(y(0),y(value)),bw,Math.max(.5,Math.abs(y(value)-y(0))))}
  for(const [key,color] of [['dif','#b86b08'],['dea','#2563eb']]){ctx.strokeStyle=color;ctx.lineWidth=1.3;ctx.beginPath();for(let i=start;i<end;i++){if(i===start)ctx.moveTo(x(i),y(indicator[key][i]));else ctx.lineTo(x(i),y(indicator[key][i]))}ctx.stroke()}
  canvas.dataset.start=String(start);canvas.dataset.end=String(end);
}
function renderDialog(){
  if(!$('chart-dialog').open||!selected)return;
  const range=chartRange(selected.bars.length,dialogCount,dialogOffset);dialogCount=range.count;dialogOffset=range.offset;
  $('dialog-title').textContent=selected.name+' · '+selected.symbol;
  $('dialog-meta').textContent=`${date(selected.bars[range.start].date)} 至 ${date(selected.bars[range.end-1].date)} · 笔 / 中枢 / 均线 / MACD`;
  $('dialog-range').textContent=`显示 ${range.count} / ${Math.min(120,selected.bars.length)} 根`;
  $('dialog-pan-back').disabled=range.offset>=selected.bars.length-range.count;$('dialog-pan-forward').disabled=range.offset<=0;
  $('zoom-in').disabled=range.count<=Math.min(12,selected.bars.length);$('zoom-out').disabled=range.count>=Math.min(120,selected.bars.length);
  dialogCoords=drawPrice(selected,$('dialog-chart'),range);drawMacd(selected,$('dialog-macd'),range);
}
function render(){
  const total=selected?selected.bars.length:0,range=chartRange(total,Number($('window').value),viewOffset);
  viewOffset=range.offset;$('pan-back').disabled=range.offset>=total-range.count;$('pan-forward').disabled=range.offset<=0;
  coords=drawPrice(selected,$('chart'),range);drawMacd(selected,$('macd-chart'),range);
  $('expand-chart').disabled=!selected;
  if(selected){
    const row=selected;
    $('title').textContent=row.name+' · '+row.symbol;$('numbers').textContent=`${date(row.date)} 收盘 ${n(row.close)}`;
    $('condition').textContent=Object.entries(row.condition.ma).map(([p,v])=>'MA'+p+' '+n(v)).join(' · ')+` · ${selectionDescription(row)} · MA5−MA10 ${n(row.condition.ma5_ma10_gap??(row.condition.ma['5']-row.condition.ma['10']))} 元 · MA5斜率 ${n(row.condition.ma_slopes?.["5"])} · MA20斜率 ${n(row.condition.ma_slopes?.["20"])} 元/根 · MA60 连续 ${row.condition.trend_bars} 次上升`;
    $('structure').textContent=`共 ${row.structure.strokes.length} 笔、${row.structure.hubs.length} 个三笔重叠中枢窗口；本批实际计算 ${total} 根日 K。MACD 使用 EMA(12,26,9)，柱为 2×(DIF−DEA)，不参与筛选。`;
  }
  renderDialog();
}
function hoverText(canvas,event,coordinate,target){
  if(!coordinate)return;
  const px=event.clientX-canvas.getBoundingClientRect().left,i=Math.max(coordinate.start,Math.min(coordinate.end-1,Math.floor((px-coordinate.left)/coordinate.step)+coordinate.start)),b=coordinate.bars[i],md=coordinate.row.macd;
  $(target).textContent=`${date(b.date)}  开 ${n(b.open)}  高 ${n(b.high)}  低 ${n(b.low)}  收 ${n(b.close)}`+(md?`  DIF ${n(md.dif[i])}  DEA ${n(md.dea[i])}  MACD ${n(md.histogram[i])}`:'');
}
function openDialog(){
  if(!selected)return;
  dialogCount=Math.min(120,selected.bars.length,Number($('window').value));dialogOffset=viewOffset;
  $('chart-dialog').showModal();renderDialog();
}
for(const id of ['chart','macd-chart']){$(id).onmousemove=event=>hoverText($(id),event,coords,'hover');$(id).ondblclick=openDialog}
for(const id of ['dialog-chart','dialog-macd'])$(id).onmousemove=event=>hoverText($(id),event,dialogCoords,'dialog-hover');
$('expand-chart').onclick=openDialog;$('close-dialog').onclick=()=>$('chart-dialog').close();
$('chart-dialog').addEventListener('click',event=>{if(event.target===$('chart-dialog'))$('chart-dialog').close()});
$('zoom-in').onclick=()=>{dialogCount=Math.max(Math.min(12,selected.bars.length),Math.floor(dialogCount*.75));renderDialog()};
$('zoom-out').onclick=()=>{dialogCount=Math.min(120,selected.bars.length,Math.ceil(dialogCount/0.75));renderDialog()};
$('zoom-reset').onclick=()=>{dialogCount=Math.min(120,selected.bars.length);dialogOffset=0;renderDialog()};
$('dialog-pan-back').onclick=()=>{dialogOffset+=Math.max(1,Math.floor(dialogCount/2));renderDialog()};
$('dialog-pan-forward').onclick=()=>{dialogOffset-=Math.max(1,Math.floor(dialogCount/2));renderDialog()};
$('search').oninput=()=>{listLimit=200;renderList()};
for(const id of ['window','showMA','showBi','showHub'])$(id).onchange=()=>{if(id==='window')viewOffset=0;render()};
$('pan-back').onclick=()=>{viewOffset+=Math.max(1,Math.floor(Number($('window').value)/2));render()};
$('pan-forward').onclick=()=>{viewOffset-=Math.max(1,Math.floor(Number($('window').value)/2));render()};
window.addEventListener('resize',render);
selected=data.rows[0]||null;renderList();render();

const serviceBase=location.protocol==='file:'?'http://127.0.0.1:8765':location.origin;
let pollTimer=null,followScan=false,scanEpoch=0,reconnectAttempts=0;
$('dashboard-link').href=serviceBase+'/';
function statusMessage(message,{error=false,running=false,done=0,total=0,help=false}={}){
  $('scan-status').hidden=false;
  $('scan-status').classList.toggle('error',error);
  $('scan-message').textContent=message;
  $('service-help').hidden=!help;
  $('scan-progress').hidden=!running;
  if(total>0){$('scan-progress').max=total;$('scan-progress').value=Math.min(done,total)}
  else $('scan-progress').removeAttribute('value');
  $('rescan').disabled=running;
  $('rescan').textContent=running?'扫描中…':'重新扫描';
}
async function scannerRequest(path,options={}){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
  try{
    const response=await fetch(serviceBase+path,{cache:'no-store',...options,signal:controller.signal});
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error||payload.message||'扫描服务请求失败');
    return payload;
  }finally{clearTimeout(timer)}
}
function showScanStatus(status){
  const running=status.state==='running',done=Number(status.done)||0,total=Number(status.total)||0;
  if(running){
    followScan=true;
    const stage={list:'正在获取全市场股票列表',scan:'正在扫描日 K',save:'正在保存结果'}[status.stage]||'正在计算笔与中枢';
    statusMessage(total>0?`${stage}：${done} / ${total}`:(status.message||stage),{running:true,done,total});
    clearTimeout(pollTimer);pollTimer=setTimeout(()=>pollScan(true),1500);
  }else if(status.state==='success'&&followScan){
    const target=status.report_url;
    if(typeof target==='string'&&/^\/results\/[A-Za-z0-9_-]+\/report\.html$/.test(target)){
      statusMessage('扫描完成，正在打开最新结果…');
      location.href=serviceBase+target;
    }else statusMessage('扫描已完成，请打开扫描页查看最新结果。',{help:true});
  }else if(status.state==='failed'){
    followScan=false;statusMessage('本次扫描失败，保留上次结果。'+(status.error||status.message||''),{error:true});
  }else if(status.state==='idle'&&followScan){
    followScan=false;statusMessage('扫描服务已就绪，点击“重新扫描”开始。');
  }else if(location.protocol==='file:'){
    statusMessage('已连接本机扫描服务，可重新扫描全市场。');
  }
}
async function pollScan(showError=false,epoch=scanEpoch){
  try{
    const status=await scannerRequest('/api/scan/status');
    if(epoch!==scanEpoch)return;
    reconnectAttempts=0;showScanStatus(status);
  }
  catch(error){
    if(epoch!==scanEpoch)return;
    if(followScan&&reconnectAttempts<8){
      reconnectAttempts++;
      statusMessage('连接暂时中断，正在重新获取扫描进度；当前报告已保留。',{error:true,running:true});
      clearTimeout(pollTimer);pollTimer=setTimeout(()=>pollScan(true,epoch),Math.min(10000,1500*reconnectAttempts));
    }else if(showError){
      followScan=false;statusMessage('未能连接扫描服务，当前报告已保留。',{error:true,help:true});
    }
  }
}
$('rescan').onclick=async()=>{
  clearTimeout(pollTimer);followScan=true;reconnectAttempts=0;const epoch=++scanEpoch;
  statusMessage('正在启动全市场扫描…',{running:true});
  try{
    const status=await scannerRequest('/api/scan',{method:'POST',headers:{'Content-Type':'application/json','X-Scanner-Request':'1'},body:'{}'});
    if(epoch!==scanEpoch)return;
    showScanStatus(status);
    if(status.state!=='running')await pollScan(true,epoch);
  }catch(error){
    if(epoch!==scanEpoch)return;
    statusMessage('正在确认扫描是否已启动…',{running:true});
    await pollScan(true,epoch);
  }
};
void pollScan(false);
</script></body></html>'''
