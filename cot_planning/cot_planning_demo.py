from __future__ import annotations

import os
import sys

from .cot_planner import CotPlanner
from .thinking_client import ThinkingModelClient, ThinkingModelConfig


def build_demo_schema_context() -> str:
    """
    构造 Demo Schema 图文本。

    实际项目中，这里直接传入：
        schema_graph.to_prompt_context()
    """
    return """数据库：trade_db
表名：trade_summary
表格摘要：该表记录用户交易汇总信息，包括用户累计交易笔数、交易金额以及交易活跃度等内容。
字段：
- 字段名：total_trade_count
  数据类型：int
  字段含义：用户累计交易总笔数
  样例值：120，5800，56000，102430
  取值范围：>=0
- 字段名：user_id
  数据类型：int
  字段含义：用户唯一标识

表名：interest_info
表格摘要：该表记录用户利率信息，包括用户利率类型、利率数值以及利率生效状态等内容。
字段：
- 字段名：interest_rate
  数据类型：decimal
  字段含义：用户对应年化利率
  样例值：2.35，3.12，4.58
  取值范围：0-100
- 字段名：user_id
  数据类型：int
  字段含义：用户唯一标识

表关联关系：
- trade_summary.user_id ↔ interest_info.user_id；关联含义：通过用户ID关联用户交易汇总信息与用户利率信息"""


def main() -> None:
    """
    运行流式 Demo。

    不配置 DASHSCOPE_API_KEY 时走 Mock 流式输出。
    配置后调用真实思考模型流式输出。

    示例：
        export DASHSCOPE_API_KEY="你的APIKey"
        export DASHSCOPE_COT_MODEL="qwen-plus"
        python -m cot_planning.cot_planning_demo
    """
    planner = CotPlanner(
        thinking_client=ThinkingModelClient(
            ThinkingModelConfig(
                api_key=os.getenv("DASHSCOPE_API_KEY", ""),
                model=os.getenv("DASHSCOPE_COT_MODEL", "qwen-plus"),
                temperature=0.0,
                use_mock_when_no_api_key=True,
                mock_stream_delay=0.01,
            )
        )
    )

    user_query = "查询总交易笔数大于50000的利率是多少"
    schema_context = build_demo_schema_context()

    print("=" * 80)
    print("CoT Planning Stream Output")
    print("=" * 80)

    chunks = []

    for chunk in planner.plan_stream(
        user_query=user_query,
        schema_graph=schema_context,
    ):
        print(chunk, end="", flush=True)
        chunks.append(chunk)

    print()

    raw_output = "".join(chunks)

    print("\n" + "=" * 80)
    print("Parsed Steps")
    print("=" * 80)

    steps = planner.parse_steps(raw_output)

    for step in steps:
        print(f"步骤{step.step_no}:")
        print(f"数据库: {step.database}")
        print(f"处理对象: {step.processing_objects}")
        print(f"操作指令: {step.operation_instruction}")
        print(f"输出目标: {step.output_target}")

    if not steps:
        print("未解析到结构化步骤，请检查模型输出格式。", file=sys.stderr)


if __name__ == "__main__":
    main()
