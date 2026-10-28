"""A-share universe and daily bars, using only Python's standard library.

Endpoint selection follows Stock_workDashboard/core/datasource.py at commit
4aacef693c87e9cab857ef23b11089dfd49cc64d; pagination, validation, and completed-bar
handling are implemented here. The Eastmoney universe explicitly covers SH/SZ/BJ.
Sina's adjustment policy is provider_default, not a promise of forward adjustment.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as wall_time, timedelta, timezone
import json
import math
import re
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode


CHINA_TZ = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
UNIVERSE_PAGE_SIZE = 500
MAX_UNIVERSE_PAGES = 100


class MarketDataError(RuntimeError):
    """An unavailable or malformed upstream response."""


class MarketListError(MarketDataError):
    """A complete A-share universe could not be verified."""


def normalize_symbol(code):
    """Keep explicit exchanges; infer common six-digit A-share prefixes."""
    if not isinstance(code, str):
        raise ValueError("股票代码必须为字符串，以保留前导零")
    value = code.strip().lower()
    if re.fullmatch(r"(?:sh|sz|bj)[0-9]{6}", value):
        return value
    if not re.fullmatch(r"[0-9]{6}", value):
        raise ValueError("股票代码须为6位数字或sh/sz/bj加6位数字")
    if value.startswith("6"):
        return "sh" + value
    if value.startswith(("0", "3")):
        return "sz" + value
    if value.startswith(("4", "8", "9")):
        return "bj" + value
    raise ValueError("不能识别该A股代码的交易所，请提供sh/sz/bj前缀")


def _date_int(value):
    if isinstance(value, bool):
        raise ValueError("日期无效")
    text = str(value)
    if re.fullmatch(r"[0-9]{8}", text):
        parsed = datetime.strptime(text, "%Y%m%d").date()
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        parsed = date.fromisoformat(text)
    else:
        raise ValueError("日期须为YYYYMMDD或YYYY-MM-DD")
    return int(parsed.strftime("%Y%m%d"))


def validate_bars(bars):
    """Return independent, chronological OHLCV rows; never repair bad data silently."""
    if not isinstance(bars, (list, tuple)):
        raise ValueError("K线必须为数组")
    result, seen = [], set()
    for row in bars:
        if not isinstance(row, dict):
            raise ValueError("K线记录必须为对象")
        try:
            day = _date_int(row["date"])
            if day in seen:
                raise ValueError("K线存在重复日期: %s" % day)
            values = {}
            for key in ("open", "high", "low", "close", "volume"):
                value = row.get(key, 0) if key == "volume" else row[key]
                if isinstance(value, bool):
                    raise ValueError("K线%s不是有效数值" % key)
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("K线%s必须为有限数" % key)
                values[key] = number
            if any(values[key] <= 0 for key in ("open", "high", "low", "close")):
                raise ValueError("K线价格必须大于零")
            if values["volume"] < 0:
                raise ValueError("成交量不能为负")
            if not (values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]):
                raise ValueError("K线OHLC高低关系无效")
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("K线缺少字段或格式无效") from exc
        result.append({"date": day, **values})
        seen.add(day)
    result.sort(key=lambda item: item["date"])
    return result


def completed_daily_bars(bars, now=None, include_forming=False):
    """Exclude future dates, and today's row before 15:00 China time by default.

    Naive `now` values are interpreted as China local time. This is a session-time
    gate, not a holiday calendar or an assertion that the provider has finalized data.
    `include_forming=True` still excludes future dates.
    """
    if not isinstance(include_forming, bool):
        raise ValueError("include_forming必须为布尔值")
    current = now or datetime.now(CHINA_TZ)
    if not isinstance(current, datetime):
        raise ValueError("now必须为datetime")
    if current.tzinfo is None:
        current = current.replace(tzinfo=CHINA_TZ)
    current = current.astimezone(CHINA_TZ)
    today = int(current.strftime("%Y%m%d"))
    allow_today = include_forming or current.time() >= wall_time(15, 0)
    normalized = validate_bars(bars)
    kept = [row for row in normalized
            if row["date"] < today or (row["date"] == today and allow_today)]
    return kept, len(normalized) - len(kept)


class MarketData:
    def __init__(self, timeout=15, retries=2):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError("timeout必须在0到120秒之间")
        if type(retries) is not int or not 0 <= retries <= 5:
            raise ValueError("retries必须在0到5之间")
        self.timeout = timeout
        self.retries = retries
        self._context = ssl.create_default_context()

    @property
    def provenance(self):
        return {
            "universe": "eastmoney.RPT_F10_ORG_BASICINFO",
            "universe_filter": '(SECURITY_TYPE="A股")(LISTING_STATE="0")',
            "exchanges": ["sh", "sz", "bj"],
            "provider": "sina.getKLineData",
            "period": "day",
            "adjustment": "provider_default",
            "volume_unit": "shares",
        }

    def _get(self, url, referer):
        headers = {"User-Agent": "Mozilla/5.0", "Referer": referer,
                   "Accept-Encoding": "identity"}
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout,
                                            context=self._context) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise MarketDataError("行情响应超过大小上限")
                return json.loads(raw.decode("utf-8-sig"))
            except urllib.error.HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == self.retries:
                    raise MarketDataError("行情HTTP错误: %s" % exc.code) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, ssl.SSLCertVerificationError) or attempt == self.retries:
                    raise MarketDataError("行情连接失败: %s" % reason) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise MarketDataError("行情响应不是有效UTF-8 JSON") from exc
            time.sleep(0.25 * (2 ** attempt))
        raise AssertionError("unreachable")

    def _universe_page(self, page):
        params = {
            "reportName": "RPT_F10_ORG_BASICINFO",
            "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR",
            "filter": '(SECURITY_TYPE="A股")(LISTING_STATE="0")',
            "sortColumns": "SECUCODE", "sortTypes": 1,
            "pageSize": UNIVERSE_PAGE_SIZE, "pageNumber": page,
        }
        try:
            payload = self._get("https://datacenter-web.eastmoney.com/api/data/v1/get?"
                                + urlencode(params), "https://data.eastmoney.com/")
            if not isinstance(payload, dict) or payload.get("success") is not True:
                raise ValueError("市场接口未返回成功状态")
            result = payload.get("result")
            if (not isinstance(result, dict) or not isinstance(result.get("data"), list)
                    or type(result.get("count")) is not int or result["count"] <= 0
                    or type(result.get("pages")) is not int or result["pages"] <= 0):
                raise ValueError("市场分页结构无效或市场列表为空")
            return result
        except Exception as exc:
            raise MarketListError("市场列表第%d页失败: %s" % (page, exc)) from exc

    def list_symbols(self):
        """Fetch every declared page and reject inconsistent, incomplete universes."""
        first = self._universe_page(1)
        count, pages = first["count"], first["pages"]
        if pages > MAX_UNIVERSE_PAGES or pages != (count + UNIVERSE_PAGE_SIZE - 1) // UNIVERSE_PAGE_SIZE:
            raise MarketListError("市场分页元信息不一致或超出上限")
        all_pages = [first]
        with ThreadPoolExecutor(max_workers=6) as executor:
            all_pages.extend(executor.map(self._universe_page, range(2, pages + 1)))
        rows = []
        for number, result in enumerate(all_pages, 1):
            expected = min(UNIVERSE_PAGE_SIZE, count - (number - 1) * UNIVERSE_PAGE_SIZE)
            if result["count"] != count or result["pages"] != pages:
                raise MarketListError("分页期间市场列表发生变化，请重试")
            if len(result["data"]) != expected:
                raise MarketListError("市场第%d页记录不足，不能确认全市场覆盖" % number)
            rows.extend(result["data"])
        symbols, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise MarketListError("市场标的不是对象")
            match = re.fullmatch(r"([0-9]{6})\.(SH|SZ|BJ)", str(row.get("SECUCODE", "")))
            if not match or str(row.get("SECURITY_CODE")) != match[1]:
                raise MarketListError("市场证券标识缺失或交易所不一致")
            symbol = normalize_symbol(match[2].lower() + match[1])
            if symbol in seen:
                raise MarketListError("市场列表含重复标的: %s" % symbol)
            name = row.get("SECURITY_NAME_ABBR")
            if not isinstance(name, str) or not name.strip():
                raise MarketListError("市场标的缺少名称: %s" % symbol)
            seen.add(symbol)
            symbols.append({"symbol": symbol, "name": name.strip()})
        if {item["symbol"][:2] for item in symbols} != {"sh", "sz", "bj"}:
            raise MarketListError("市场列表未同时覆盖沪深北，不能标记为A股全市场")
        return sorted(symbols, key=lambda item: item["symbol"])

    def daily_bars(self, symbol, count=120):
        symbol = normalize_symbol(symbol)
        if type(count) is not int or not 30 <= count <= 5000:
            raise ValueError("count必须为30到5000之间的整数")
        params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": count}
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "CN_MarketData.getKLineData?" + urlencode(params))
        payload = self._get(url, "https://finance.sina.com.cn/")
        if not isinstance(payload, list) or not payload:
            raise MarketDataError("新浪未返回%s的日K线" % symbol)
        rows = []
        try:
            for row in payload:
                if not isinstance(row, dict):
                    raise ValueError("K线记录不是对象")
                raw_day = str(row["day"])
                # Sina's daily endpoint may include a time component.
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2}:[0-9]{2})?", raw_day):
                    raise ValueError("新浪K线日期格式无效")
                rows.append({"date": raw_day[:10], "open": row["open"],
                             "high": row["high"], "low": row["low"],
                             "close": row["close"], "volume": row.get("volume", 0)})
            return validate_bars(rows)[-count:]
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("%s日K线校验失败: %s" % (symbol, exc)) from exc
