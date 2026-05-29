"""predict_budget — budget exhaustion prediction across token/time/steps dimensions."""

from __future__ import annotations

import json
import time
from typing import Any

from openagents.errors.exceptions import PermanentToolError
from openagents.interfaces.tool import ToolExecutionSpec, ToolPlugin


class PredictBudgetTool(ToolPlugin):
    """Predict budget exhaustion based on current consumption trends."""

    name = "predict_budget"
    description = (
        "基于当前消耗趋势，预测预算何时耗尽。"
        "支持 token、time、steps 三个维度的独立预测和综合判断。"
    )
    durable_idempotent = True

    def execution_spec(self) -> ToolExecutionSpec:
        return ToolExecutionSpec(concurrency_safe=True, side_effects="read_only")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "enum": ["token", "time", "steps", "all"],
                    "default": "all",
                    "description": "预测维度",
                },
            },
        }

    async def invoke(self, params: dict[str, Any], context: Any) -> str:
        deps = getattr(context, "deps", None)
        board = getattr(deps, "state_board", None) if deps else None
        if board is None:
            raise PermanentToolError("StateBoard not available", tool_name=self.name)

        dim = params.get("dimension", "all")
        elapsed = time.time() - board.budget.start_time
        predictions = {}

        if dim in ("token", "all"):
            token_rate = board.budget.token_used / max(elapsed, 1)
            remaining = board.budget.token_remaining
            if token_rate > 0:
                eta_token_s = remaining / token_rate
            else:
                eta_token_s = float("inf")

            predictions["token"] = {
                "current": board.budget.token_used,
                "limit": board.budget.token_limit,
                "remaining": remaining,
                "consumption_rate_per_second": round(token_rate, 2),
                "estimated_exhaustion_seconds": round(eta_token_s, 1) if eta_token_s != float("inf") else "unknown",
                "at_current_pace": (
                    "will_exhaust" if eta_token_s < board.budget.time_remaining_s else "time_limited"
                ),
            }

        if dim in ("time", "all"):
            predictions["time"] = {
                "current_elapsed_s": round(elapsed, 1),
                "limit_s": board.budget.time_limit_s,
                "remaining_s": round(board.budget.time_remaining_s, 1),
                "pct_used": round(elapsed / board.budget.time_limit_s * 100, 1),
            }

        if dim in ("steps", "all"):
            predictions["steps"] = {
                "current": board.budget.steps_taken,
                "limit": board.budget.max_steps,
                "remaining": board.budget.max_steps - board.budget.steps_taken,
                "pct_used": round(board.budget.steps_taken / board.budget.max_steps * 100, 1),
            }

        # 综合判断
        all_dims = []
        if "token" in predictions:
            eta = predictions["token"].get("estimated_exhaustion_seconds")
            if isinstance(eta, (int, float)):
                all_dims.append(("token", eta))
        if "time" in predictions:
            all_dims.append(("time", predictions["time"]["remaining_s"]))
        if "steps" in predictions:
            all_dims.append(("steps", predictions["steps"]["remaining"]))

        if all_dims:
            first_to_go = min(all_dims, key=lambda x: x[1])
            recommendations = {
                "token": "减少LLM调用频率或合并请求",
                "time": "加快决策速度，减少等待",
                "steps": "合并任务减少step消耗",
            }
            predictions["first_to_exhaust"] = first_to_go[0]
            predictions["recommendation"] = recommendations.get(first_to_go[0], "检查预算配置")

        return json.dumps(predictions, indent=2, ensure_ascii=False)
