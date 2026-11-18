"""数据工具：获取真实财报、校验数据并计算同比，供 Agent 调用。

代码从上到下分为：
1. COMPANIES、FIELDS：定义支持的公司，以及程序字段与数据源字段的对应关系。
2. today_shanghai、validate_request：确定当前日期，检查股票代码和研究日期。
3. number_or_none、yoy_percent：检查数值，计算同报告期同比。
4. build_snapshot：处理已取得的数据，筛选财报、匹配去年同期并整理证据。
5. get_financial_snapshot：请求公开接口，再调用 build_snapshot 返回财报快照。
6. TOOL_SCHEMA：告诉模型工具叫什么、能做什么、需要哪些参数。

实际取数顺序：get_financial_snapshot → 公开接口 → build_snapshot → 返回结构化证据。
本文件不调用模型；数值计算由 Python 完成。
"""

import json
import math
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone


# 支持的公司：键是股票代码字符串（保留开头的 0），值是公司名称。
COMPANIES = {"000333": "美的集团"}
# FIELDS 是“内部字段名 → 数据接口字段名”的映射，只改名称，不换算单位。
FIELDS = {
    "revenue_yuan": "TOTAL_OPERATE_INCOME",  # 营业总收入，单位：人民币元。
    "parent_net_profit_yuan": "PARENT_NETPROFIT",  # 归母净利润，单位：人民币元。
}


def today_shanghai():
    # timedelta(hours=8) 表示相对 UTC 偏移 +8 小时；date 取日期，isoformat 转 YYYY-MM-DD。
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def validate_request(symbol, as_of):#检查股票代码和研究日期
    # symbol：股票代码字符串；as_of：研究截止日期字符串，格式 YYYY-MM-DD。
    if symbol not in COMPANIES:
        raise ValueError("第一版只支持美的集团 000333。")
    try:
        # cutoff：研究截止日期的 date 对象，可与其他日期直接比较。
        cutoff = date.fromisoformat(as_of)#把用户传入的日期字符串解析成date对象
    except (TypeError, ValueError) as exc:  # exc：捕获到的异常对象，用于保留错误原因。
        raise ValueError("as_of 必须是 YYYY-MM-DD 格式。") from exc
    if cutoff.isoformat() != as_of or as_of > today_shanghai():
        raise ValueError("研究日期必须为 YYYY-MM-DD，且不得晚于上海当前日期。")
    return cutoff


def number_or_none(value):#进行数字防御，对外部接口返回的每个数值都验明正身（是数字、不是布尔、不是 NaN）
    # value：待校验的财报数值；None 表示数据缺失。
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("财报数值不是 JSON 数字。")
    if not math.isfinite(value):
        raise ValueError("财报数值不是有限数字。")
    return value


def yoy_percent(current, previous):
    # current：本期数值；previous：去年同报告期数值；返回百分数，20 表示增长 20%。
    # 亏损基期不直接给一个容易误导的“增长率”。
    if current is None or previous is None or previous <= 0:
        return None
    return round((current / previous - 1) * 100, 4)


def build_snapshot(rows, symbol, as_of, source_url):
    """纯数据处理函数，不联网；按披露日期和报告期双重过滤。"""
    # rows：接口返回的财报记录列表；source_url：本次数据请求的完整来源地址。
    cutoff = validate_request(symbol, as_of)  # 研究截止日期的 date 对象，用来比较日期。
    eligible = []  # 通过股票、日期检查的记录；每项是（报告期末、披露日、原始记录）。
    for row in rows:  # row：一条原始财报记录，类型是字典。
        if row.get("SECURITY_CODE") != symbol:  # SECURITY_CODE：接口中的股票代码。
            continue
        try:
            report_date = date.fromisoformat(str(row["REPORT_DATE"])[:10])  # REPORT_DATE：报告期末。
            notice_date = date.fromisoformat(str(row["NOTICE_DATE"])[:10])  # NOTICE_DATE：披露日期。
        except (KeyError, ValueError):
            continue  # 无法确定披露时间的行不进入证据。
        if report_date > cutoff or notice_date > cutoff or notice_date < report_date:
            continue
        eligible.append((report_date, notice_date, row))
    if not eligible:
        raise ValueError("研究日期前没有可用且披露时间明确的财报。")
    # item：列表中的一组记录；按报告期末、披露日降序排列，最新记录在前。
    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    report_date, notice_date, current = eligible[0]  # current 在本函数中是最新财报的原始字典。
    previous_date = report_date.replace(year=report_date.year - 1)  # 去年同报告期末。
    previous_match = next((item for item in eligible if item[0] == previous_date), None)  # 去年同期记录，可能没有。

    def extract(item):
        period, notice, raw = item  # period：报告期末；notice：披露日；raw：原始财报字典。
        return {
            "report_date": period.isoformat(),  # 输出的报告期末，例如 2026-09-30。
            "notice_date": notice.isoformat(),  # 输出的披露日期；与报告期末不同。
            # name：程序内部字段名；field：对应的接口字段名；** 把映射结果展开进本字典。
            **{name: number_or_none(raw.get(field)) for name, field in FIELDS.items()},
        }

    latest = extract((report_date, notice_date, current))  # 整理后的最新财报字典。
    # key：FIELDS 中的一个内部字段名，用它检查对应金额是否缺失。
    if any(latest[key] is None for key in FIELDS):
        raise ValueError("最新财报缺少收入或归母净利润，不能生成完整快照。")
    previous = extract(previous_match) if previous_match else None  # 整理后的去年同期财报，缺失时为 None。
    growth = {  # 计算结果字典；字段名由金额字段名替换后缀生成。
        # revenue_yoy_percent：收入同比；parent_net_profit_yoy_percent：归母净利润同比；单位均为 %。
        key.replace("_yuan", "_yoy_percent"): yoy_percent(
            latest[key], previous[key] if previous else None
        )
        for key in FIELDS
    }
    return {
        "evidence_id": f"{symbol}:{report_date.isoformat()}:{notice_date.isoformat()}",  # 本条证据的引用标识。
        "symbol": symbol,  # 股票代码。
        "company": COMPANIES[symbol],  # 公司名称。
        "as_of": as_of,  # 本次研究截止日期。
        "as_of_convention": "按指定日期收盘后/日终口径筛选，不能用于当日盘中回测。",  # 截止日期的使用口径。
        "unit": "人民币元；同比单位为百分比，例如 5 表示 5%。",  # 数值单位说明。
        "period_basis": "年初至报告期末累计数；年报为全年，半年报为半年，三季报为前三季度。",  # 财报期间口径。
        "latest": latest,  # 最新财报内容。
        "previous_same_period": previous,  # 去年同报告期的财报内容。
        "computed": growth,  # 由 Python 计算的同比指标。
        "source": {  # 数据来源信息，与后面请求参数中的 source 标记含义不同。
            "name": "东方财富数据中心，第三方财报汇总接口",  # 数据源名称。
            "url": source_url,  # 可复查的接口请求地址。
            "retrieved_at": datetime.now(timezone.utc).isoformat(),  # 实际取数时间，UTC；不是披露时间。
        },
        "limitations": [  # 本份数据的缺口、限制和使用边界。
            "第一版仅包含营业总收入、归母净利润和同报告期同比。",
            "没有现金流、资产负债表、股价、估值和完整公告，不能据此认定盈利质量或给出买卖结论。",
            "同比为空表示基期缺失、数值缺失或基期非正数，不能由模型补造。",
            "日期过滤不是历史版本数据库；后续更正或重述的数据可能已覆盖旧值，不保证历史回测无泄漏。",
            "数据待与公司原始公告交叉核验；当前工具不自动完成原始公告核验。",
        ],
    }


def get_financial_snapshot(symbol, as_of):
    """Agent 可调用的工具。股票代码和研究日期均由宿主程序再次校验。"""
    validate_request(symbol, as_of)
    params = {  # 发送给财报接口的 URL 查询参数。
        "reportName": "RPT_DMSK_FN_INCOME",  # 数据表名称：上市公司利润表。
        "columns": "SECURITY_CODE,REPORT_DATE,NOTICE_DATE,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT",  # 要返回的字段。
        "filter": f'(SECURITY_CODE="{symbol}")(NOTICE_DATE<=\'{as_of}\')(REPORT_DATE<=\'{as_of}\')',  # 股票与日期筛选条件。
        "sortColumns": "REPORT_DATE",  # 用报告期末作为排序字段。
        "sortTypes": "-1",  # -1 表示降序，即最新报告在前。
        "pageSize": "100",  # 每页最多返回 100 条记录。
        "pageNumber": "1",  # 请求第 1 页。
        "source": "WEB",  # 数据接口要求的来源标记，不是证据来源字典。
        "client": "WEB",  # 数据接口要求的客户端标记。
    }
    # urlencode 把 params 编成 URL 查询字符串；source_url 是最终请求地址。
    source_url = "https://datacenter-web.eastmoney.com/api/data/v1/get?" + urllib.parse.urlencode(params)
    # request：HTTP 请求对象；headers：请求头；User-Agent：标识发请求的程序。
    request = urllib.request.Request(source_url, headers={"User-Agent": "stock-agent-learning/0.1"})
    with urllib.request.urlopen(request, timeout=25) as response:  # response：HTTP 响应；timeout 单位为秒。
        payload = json.load(response)  # payload：接口完整响应解析后的 Python 字典。
    result = payload.get("result") or {}  # result：响应中承载查询结果的字典。
    rows = result.get("data")  # data：查询结果中的财报记录列表。
    # success：接口是否查询成功；message：接口给出的说明或错误原因。
    if not payload.get("success") or not isinstance(rows, list):
        raise ValueError("数据源返回失败或格式变化：" + str(payload.get("message", "未知错误")))
    if result.get("pages", 1) > 1:  # pages：结果总页数；当前只允许一页。
        raise ValueError("数据超过一页；第一版不静默忽略后续页。")
    return build_snapshot(rows, symbol, as_of, source_url)


TOOL_SCHEMA = {  # 工具说明书：发给模型，描述工具与参数；它本身不会执行函数。
    "type": "function",  # 工具类型：由本地 Python 执行的函数。
    "name": "get_financial_snapshot",  # 模型调用工具时使用的函数名称。
    "description": "获取美的集团指定研究日期前披露的最近一份财报收入、归母净利润及同报告期同比。工具提供证据来源和数据局限。",  # 工具用途。
    "strict": True,  # 要求模型按参数结构输出；程序还会独立校验。
    "parameters": {  # 工具参数的 JSON Schema，描述参数应满足的结构。
        "type": "object",  # 整组参数必须是一个 JSON 对象。
        "properties": {  # 对象允许包含的参数及各自规则。
            "symbol": {"type": "string", "enum": ["000333"]},  # string：字符串；enum：允许的取值列表。
            "as_of": {"type": "string", "description": "研究日期，YYYY-MM-DD，按该日日终口径。"},  # 日期也以字符串传递。
        },
        "required": ["symbol", "as_of"],  # 两个参数都必须提供。
        "additionalProperties": False,  # 不允许夹带未声明的参数。
    },
}
