# 第一课：单 Agent 财报研究最小闭环

目标：**用户问题 → 模型决定调用工具 → Python 获取真实数据并计算 → 工具结果回传模型 → 分析和运行记录落盘。**

当前只研究美的集团（000333），只分析营业总收入、归母净利润和同报告期同比。暂不加入多 Agent、行情择时、RAG、数据库或 Web 后端。

## 文件与阅读顺序

1. `tools.py`：先看 `get_financial_snapshot`，理解 API 请求；再看 `build_snapshot`，理解日期、单位、累计期间和同比。
2. `agent.py`：看 `run_agent`，理解 `function_call`、`call_id`、`function_call_output` 和循环退出。
3. `test_agent.py`：合成样本验证工具的日期边界与调用协议；它不代表真实模型已经通过。

Python 3.10+，只使用标准库，不需要安装依赖。本目录可复制到 Ubuntu 项目环境后用 VS Code 编辑、Ubuntu 终端运行；也可在现有 Windows Python 下运行。

## 第一步：单独验证真实工具

在本目录运行：

```bash
python agent.py --check-data --as-of 2026-11-12
```

查看 `runs/<本次编号>/evidence.json`：

- `report_date` 是报告期末，`notice_date` 是披露日期，二者不能混淆。
- 金额单位为人民币元；三季报数据是年初至三季度末累计数，不能称为第三季度单季度。
- 同比使用去年**同一报告期**，不是上一季度。
- 无前一年同期数据或基期非正时，同比增长为空，不让模型编造。
- `source.url` 可用于复查接口，`retrieved_at` 是本次获取时间。

公开数据源是东方财富的第三方财报汇总接口，可能调整或不可用。当前未逐项与交易所/公司原始公告交叉核验。

研究日期按日终口径过滤，今天的数据不能用于今天开盘前的决策。日期过滤也不能恢复历史更正前的版本，因此第一版不承诺历史回测无前视偏差。

## 第二步：配置已有的原生 Responses API

按用户提供的截图，接口地址和模型为：

- Base URL：`http://114.212.84.8:8080/v1`
- 模型：`gpt-6.1-sol`
- 请求协议：原生 Responses，程序访问 `/v1/responses`。

这是用户提供的第三方网关配置；网关是否支持完整工具调用需实际验证。程序不自动切换 Chat Completions，也不替换模型。

**不要把密钥写到源码、聊天或 Git。** `.env.example` 只是配置说明，本程序不自动加载 `.env`。

Ubuntu / Bash：

```bash
export STOCK_AGENT_BASE_URL='http://114.212.84.8:8080/v1'
export STOCK_AGENT_MODEL='gpt-6.1-sol'
read -r -s -p 'API key: ' STOCK_AGENT_API_KEY
export STOCK_AGENT_API_KEY
printf '\n'
python agent.py --as-of 2026-11-12
```

Windows / PowerShell：

```powershell
$env:STOCK_AGENT_BASE_URL = 'http://114.212.84.8:8080/v1'
$env:STOCK_AGENT_MODEL = 'gpt-6.1-sol'
$agentSecureKey = Read-Host 'API key' -AsSecureString
$env:STOCK_AGENT_API_KEY = [System.Net.NetworkCredential]::new('', $agentSecureKey).Password
python agent.py --as-of 2026-11-12
Remove-Item Env:STOCK_AGENT_API_KEY
```

每次最多 6 次模型请求、4 次工具调用，模型请求有超时和输出长度限制。这个最小示例是非流式调用，不把之前推理项目中的 SSE 代码硬塞进来。

## 第三步：验收与学习

成功运行应产生：

```text
runs/<本次编号>/
  run.json       # 模式、研究日期、成功/失败状态
  trace.json     # 模型实际工具调用、工具返回、模型最终输出
  evidence.json  # 本次真实工具证据
  report.md      # 仅真实模型闭环成功时生成
```

`--check-data` 仅生成真实工具结果，不生成报告。密钥未配置、工具未成功、模型直接回答、接口格式不匹配时，`run.json` 标记失败，不用模板或模拟模型伪造成功报告。

验收时打开 `trace.json` 核对：

1. 模型实际发出 `get_financial_snapshot`，股票和日期与用户任务一致。
2. 工具返回真实来源、期间、单位和同比；同一个 `call_id` 对应工具结果回传。
3. 模型读取工具结果后输出分析；逐条人工核对数字、期间、来源及事实/推断是否分开。
4. 没有现金流和估值时，报告明确缺口；不能把这份快照当成完整投资结论。

`ok=true` 只表示调用闭环完成，**不表示财报解释或投资判断已验收正确**。

离线协议和边界测试：

```bash
python -m unittest -v
```

这些测试使用合成数据与模拟模型，只验证程序逻辑；真实数据验证和真实模型验收分别记录。

## 本次实现状态

- 已读取截图中的接口地址、模型和协议，未获取密钥。
- 2026-11-12 已运行 `python agent.py --check-data --as-of 2026-11-12`，真实接口、日期过滤、同期匹配及结果保存通过；不代表原始公告已交叉核验。
- `python -m unittest -v` 的 8 项测试通过，包括披露日期过滤、缺失数据、非正基期、Responses 工具回传、日期不可篡改及无证据回答拒绝；模型协议测试使用模拟响应。
- 真实模型端到端调用仍需本机配置密钥后验收。
- OpenAI Docs 技能要求核对官方工具调用文档；本环境访问两个官方文档地址均返回 HTTP 403，未把搜索摘要当成文档核实结果。原生 Responses 适配实现需要以网关实测为准。
