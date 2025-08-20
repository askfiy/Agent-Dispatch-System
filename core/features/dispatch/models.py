import datetime
from typing import Any
from pydantic import Field

from core.shared.enums import AgentTaskState
from core.shared.base.models import BaseModel, LLMOutputModel, LLMTimeField
from . import prompt


class TaskDispatchLLMModel(BaseModel):
    model_name: str
    api_key: str


class TaskDispatchCreateModel(BaseModel):
    owner: str
    original_user_input: str
    owner_timezone: str = Field(default="UTC", exclude=True)
    session_id: str
    mcp_server_infos: dict[str, Any]


class TaskDispatchRefactorModel(BaseModel):
    task_id: int
    update_user_prompt: str


class TaskDispatchGeneratorInfoOutput(LLMOutputModel):
    is_splittable: bool = Field(
        description="Indicates whether a task needs to be created", examples=[True]
    )

    name: str = Field(
        description="The name of the task. A name is not necessary if no task needs to be created.",
        examples=["Schedule meeting attendance"],
    )

    expect_execute_time: LLMTimeField = Field(
        description="The expected execution time of the task. If the task is for **immediate execution**, the time should be the current time. If no task needs to be created, this time should not be calculated. Requirement: **Strictly output in UTC time**.",
    )

    keywords: list[str] = Field(
        description="Task keywords, which must be in plain English. Keywords are not necessary if no task needs to be created.",
        examples=["timed", "feature", "meeting", "JohnDoe"],
    )

    prd: str = Field(
        description="The Product Requirements Document (PRD). It must include background, objectives, description, execution plan, **Respect the user's language. The output PRD must be in the user's language.** etc. A PRD is not necessary if no task needs to be created.",
        examples=[prompt.get_prd_example()],
    )


class TaskDispatchRefactorInfoOutput(LLMOutputModel):
    name: str = Field(
        description="The name of the task",
        examples=["Schedule meeting attendance"],
    )

    expect_execute_time: LLMTimeField = Field(
        description="The expected execution time of the task. If it is an **immediate execution** task, use the current time. If it is a **delayed execution task**, calculate its next execution time. Requirement: **Strictly output in UTC time**.",
    )

    keywords: list[str] = Field(
        description="Task keywords, which must be in plain English.",
        examples=["timed", "feature", "meeting", "JohnDoe"],
    )

    prd: str = Field(
        description="The Product Requirements Document (PRD). It must include background, objectives, description, execution plan, etc. A PRD is not necessary if no task needs to be created.",
        examples=[prompt.get_prd_example()],
    )


class TaskDispatchGeneratorPlanningOutput(LLMOutputModel):
    process: str = Field(
        description="The task execution plan. It must be a clear and executable Markdown document.",
        examples=[prompt.get_process_example()],
    )


class TaskDispatchUpdatePlanningOutput(LLMOutputModel):
    process: str = Field(
        description="The updated task execution plan.",
        examples=[prompt.get_process_example()],
    )


class TaskDispatchUpdatePlanningInput(BaseModel):
    process: str
    notify_user: str
    user_message: str


class TaskDispatchExecuteUnitInput(BaseModel):
    name: str = Field(
        description="The name of the execution unit",
        examples=["Prepare meeting presentation"],
    )
    objective: str = Field(
        description="The objective of the execution unit",
        examples=[
            "Produce a clear meeting presentation. It should include an analysis of Q3 sales performance, product optimization directions, and the final expected revenue."
        ],
    )


class TaskUnitDispatchInput(BaseModel):
    name: str = Field(
        description="The name of the execution unit",
        examples=["Prepare meeting presentation"],
    )
    objective: str = Field(
        description="The objective of the execution unit",
        examples=[
            "Produce a clear meeting presentation. It should include an analysis of Q3 sales performance, product optimization directions, and the final expected revenue."
        ],
    )

    output: str = Field(
        description="Must be a user-facing, clear report of the execution result.",
        examples=[prompt.get_unit_output_example()],
    )
    created_at: datetime.datetime = Field(
        description="The time when the content of the execution unit was produced."
    )


class TaskDispatchGeneratorExecuteUnitOutput(LLMOutputModel):
    unit_list: list[TaskDispatchExecuteUnitInput] = Field(
        description="A list containing the execution units"
    )


class TaskDispatchExecuteUnitOutput(LLMOutputModel):
    output: str = Field(
        description="The execution result of the unit; a user-facing, clear report of the execution result.",
        examples=[prompt.get_unit_output_example()],
    )


class TaskDispatchGeneratorNextStateOutput(LLMOutputModel):
    process: str = Field(
        description="The updated task execution plan, including the final Output result for each execution unit.",
        examples=[prompt.get_next_process_example()],
    )
    state: AgentTaskState = Field(
        description="The new task state.", examples=[AgentTaskState.ACTIVATING.value]
    )
    notify_user: str | None = Field(
        description="Information to notify the user. This should only be filled when the new task state is 'WAITING'. This field must contain explicit content, and any information requiring user confirmation must be written in full.",
        examples=[
            "The meeting outline has been successfully drafted, but some necessary information is required to proceed with the task, such as the list of attendees, their contact information, and confirmation on whether the meeting outline meets expectations."
        ],
    )
    replenish: list[str] | None = Field(
        description="A list of information to request from the user. This should only be filled when the new task state is 'WAITING'. This field must be structured and contain a prompt for each item the user needs to provide.",
        examples=[
            "['1. Please provide the list of attendees.', '2. Please provide the contact information for the attendees. E.g., John Doe www.example.mail ...', '3. Please confirm the meeting outline.']"
        ],
    )
    next_execute_time: LLMTimeField | None = Field(
        default=None,
        description="When the state is 'scheduling', this field must be filled to calculate the next execution time. Requirement: **Strictly output in UTC time**.",
    )


class TaskDispatchGeneratorResultOutput(LLMOutputModel):
    result: str = Field(
        description="The final result.", examples=[prompt.get_result_example()]
    )


class TaskDispatchGeneratorResultInput(BaseModel):
    prd: str
    process: str
    all_units: list[TaskUnitDispatchInput]
