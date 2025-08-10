import uuid
from pydantic import Field, model_validator

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
