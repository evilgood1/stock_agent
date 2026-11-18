"""测试代码：检查数据处理和 Agent 工具调用流程，使用合成数据与模拟模型。

代码从上到下分为：
1. row：构造一条合成财报记录，方便准备测试输入。
2. SnapshotTests：检查披露日期过滤、同报告期匹配、缺失数值和非正基期。
3. LoopTests：模拟模型响应，检查工具结果回传、研究日期约束和无证据回答拒绝。
4. 文件末尾的 unittest.main：直接运行本文件时执行测试。

这些测试不调用真实模型或数据接口；通过测试不代表真实模型分析已经验收。
"""

import copy
import json
import unittest

from agent import run_agent
from tools import build_snapshot, yoy_percent


def row(period, notice, revenue, profit):
    # 参数均为合成样本：period 报告期末；notice 披露日；revenue 收入；profit 归母净利润。
    # 字段与真实接口同名：SECURITY_CODE 股票代码，REPORT_DATE 报告期末，NOTICE_DATE 披露日。
    # TOTAL_OPERATE_INCOME：营业总收入；PARENT_NETPROFIT：归母净利润；测试金额单位也按元解释。
    return {"SECURITY_CODE": "000333", "REPORT_DATE": period, "NOTICE_DATE": notice,
            "TOTAL_OPERATE_INCOME": revenue, "PARENT_NETPROFIT": profit}


class SnapshotTests(unittest.TestCase):
    def test_filters_notice_and_compares_same_period(self):
        # self：当前测试实例；rows：合成财报列表，包含截止日后才能看到的记录。
        rows = [row("2025-09-30", "2025-10-30", 150, 15),
                row("2025-06-30", "2025-08-30", 120, 12),
                row("2024-06-30", "2024-08-30", 100, 10)]
        snapshot = build_snapshot(rows, "000333", "2025-09-01", "synthetic://test")  # 整理后的证据字典。
        # latest.report_date：最新可用报告期；computed.revenue_yoy_percent：计算得到的收入同比百分数。
        self.assertEqual(snapshot["latest"]["report_date"], "2025-06-30")
        self.assertEqual(snapshot["computed"]["revenue_yoy_percent"], 20)

    def test_missing_notice_cannot_be_used(self):
        with self.assertRaises(ValueError):
            build_snapshot([row("2025-06-30", None, 100, 10)], "000333", "2025-09-01", "synthetic://test")

    def test_missing_same_period_is_null(self):
        snapshot = build_snapshot([row("2025-06-30", "2025-08-30", 100, 10)], "000333", "2025-09-01", "synthetic://test")
        self.assertIsNone(snapshot["computed"]["revenue_yoy_percent"])

    def test_nonpositive_base_is_not_growth(self):
        self.assertIsNone(yoy_percent(5, -10))
        self.assertIsNone(yoy_percent(5, 0))

    def test_missing_latest_metric_fails(self):
        with self.assertRaises(ValueError):
            build_snapshot([row("2025-06-30", "2025-08-30", None, 10)], "000333", "2025-09-01", "synthetic://test")


class LoopTests(unittest.TestCase):
    def make_model(self, arguments):
        # arguments：模拟模型准备输出的工具参数字典。
        seen = []  # 保存每次模拟模型接收的输入，便于检查程序有没有正确回传结果。

        def model(items):
            # items：本轮对话输入；深拷贝后保存，避免后续追加输入影响已保存的测试证据。
            seen.append(copy.deepcopy(items))
            if len(seen) == 1:
                # output：模型输出项列表；type 标识输出项类型，以下均是合成协议样本。
                return {"output": [
                    # reasoning：推理上下文；id：输出项标识；summary：摘要；encrypted_content：加密上下文。
                    {"type": "reasoning", "id": "synthetic_reasoning", "summary": [], "encrypted_content": "synthetic"},
                    # call_id：工具调用标识；name：工具名称；arguments：序列化后的 JSON 参数字符串。
                    {"type": "function_call", "call_id": "synthetic_call", "name": "get_financial_snapshot",
                     "arguments": json.dumps(arguments)},
                ]}
            # message：最终消息；content：内容块列表；output_text：文本块；text：具体文字。
            return {"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "合成协议测试输出，不是实测模型分析。"}]}]}

        return model, seen

    def test_tool_result_and_reasoning_are_returned_to_model(self):
        # symbol：股票代码；as_of：研究日期。这两个参数必须与用户任务一致。
        model, seen = self.make_model({"symbol": "000333", "as_of": "2025-09-01"})
        trace = []  # 收集本次模拟执行过程。
        # model_request 和 financial_tool 是替代函数；kwargs 接收展开后的命名参数。
        # synthetic=True 是测试自定义标记，表示此结果为合成样本，不是真实财报。
        answer, evidence = run_agent("000333", "2025-09-01", trace,
                                    model_request=model, financial_tool=lambda **kwargs: {"synthetic": True})
        self.assertIn("合成", answer)
        self.assertEqual(evidence, {"synthetic": True})
        self.assertTrue(any(item.get("type") == "reasoning" for item in seen[1]))
        returned = seen[1][-1]  # 第二次模型请求中的最后一项，应为工具执行结果。
        # function_call_output：工具结果类型；output：结果 JSON 字符串；ok：工具执行是否成功。
        self.assertEqual(returned["type"], "function_call_output")
        self.assertEqual(returned["call_id"], "synthetic_call")
        self.assertTrue(json.loads(returned["output"])["ok"])

    def test_model_cannot_change_cutoff(self):
        model, seen = self.make_model({"symbol": "000333", "as_of": "2025-10-01"})
        calls = []  # 若执行取数函数就在此留痕；错误日期应被提前拦截，列表应保持为空。
        with self.assertRaises(RuntimeError):
            run_agent("000333", "2025-09-01", [], model_request=model,
                      financial_tool=lambda **kwargs: calls.append(kwargs))
        self.assertEqual(calls, [])
        self.assertFalse(json.loads(seen[1][-1]["output"])["ok"])

    def test_direct_answer_without_evidence_fails(self):
        with self.assertRaises(RuntimeError):
            run_agent("000333", "2025-09-01", [], model_request=lambda items: {
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "未查数据的回答"}]}]})


if __name__ == "__main__":
    unittest.main()
