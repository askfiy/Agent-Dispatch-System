import uuid
from pydantic import Field, model_validator

from core.shared.enums import AgentTaskState
from core.shared.base.models import BaseModel, LLMOutputModel, LLMTimeField
from . import prompt


class TaskDispatchCreateModel(BaseModel):
    owner: str
    original_user_input: str
    owner_timezone: str = Field(default="UTC", exclude=True)
    session_id: uuid.UUID


# ---- 输出字段
class TaskDispatchGeneratorInfoModel(LLMOutputModel):
    is_splittable: bool = Field(description="是否需要创建任务", examples=[True])

    name: str = Field(
        description="任务名称, 若不需要创建任务, 则不必生成名称",
        examples=["定时参加会议"],
    )

    expect_execute_time: LLMTimeField = Field(
        description="任务预期执行时间. 若是**立即执行**的任务, 则时间为当前时间. 若不需要创建任务. 则不必计算时间.",
    )

    keywords: list[str] = Field(
        description="任务关键字, 必须是纯英文. 若不需要创建任务. 则不必创建关键字.",
        examples=["timed", "feature", "meeting", "ZhangSan"],
    )

    prd: str = Field(
        description="需求 PRD. 必须包含背景, 目标, 描述信息, 执行计划等等. 若不需要创建任务. 则不必创建 PRD.",
        examples=[prompt.get_prd_example()],
    )


class TaskDispatchGeneratorPlanningModel(LLMOutputModel):
    process: str = Field(
        description="任务执行计划. 一个 Markdown 文档, 必须清晰, 可执行.",
        examples=[prompt.get_process_example()],
    )

class TaskDispatchUpdatePlanningModel(LLMOutputModel):
    process: str = Field(
        description="更新后的任务执行计划.",
        examples=[prompt.get_process_example()],
    )


class TaskDispatchExecuteUnitModel(BaseModel):
    name: str = Field(description="执行单元的名称", examples=["准备会议演示文稿"])
    objective: str = Field(
        description="执行单元的目标",
        examples=[
            "产出一份明确的会议文稿. 包含 Q3 季度的销售情况分析, 产品优化方向, 最终预期收益等."
        ],
    )


class TaskUnitDispatchModel(BaseModel):
    name: str = Field(description="执行单元的名称", examples=["准备会议演示文稿"])
    objective: str = Field(
        description="执行单元的目标",
        examples=[
            "产出一份明确的会议文稿. 包含 Q3 季度的销售情况分析, 产品优化方向, 最终预期收益等."
        ],
    )

    output: str = Field(
        description="必须是面向用户的、清晰的执行结果报告。",
        examples=[prompt.get_unit_output_example()],
    )


class TaskDispatchGeneratorExecuteUnitModel(LLMOutputModel):
    unit_list: list[TaskDispatchExecuteUnitModel] = Field(
        description="包含执行单元的列表"
    )


class TaskDispatchExecuteUnitOutputModel(LLMOutputModel):
    output: str = Field(
        description="执行单元的执行结果, 面向用户的、清晰的执行结果报告。",
        examples=[prompt.get_unit_output_example()],
    )


class TaskDispatchGeneratorNextStateModel(LLMOutputModel):
    process: str = Field(
        description="更新后的任务执行计划. 包含了每一个执行单元的 Output 最终结果.",
        examples=[prompt.get_next_process_example()],
    )
    state: AgentTaskState = Field(
        description="新的任务状态.", examples=[AgentTaskState.ACTIVATING.value]
    )
    notify_user: str | None = Field(
        description="需要通知用户的信息, 仅在新任务状态为 'WAITING' 时填充. 此字段必须包含明确内容, 需要用户确认的信息必须完整的写入.",
        examples=["已生成会议通知名单, 请确认是否有遗漏, 会议通知名单如下:\n1.张三\n2.李四 .."],
    )


