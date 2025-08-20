from collections.abc import Callable
from typing import Any, TypeVar

from agents import (
    Agent as BasicAgent,
    Model,
    RunContextWrapper,
    Runner,
    TContext,
)
from agents.agent_output import AgentOutputSchemaBase
from agents.items import TResponseInputItem
from agents.result import RunResult, RunResultStreaming
from agents.util._types import MaybeAwaitable
from agents.mcp import MCPServerStdio, MCPServerSse, MCPServerSseParams, MCPServer

from core.shared.base.models import LLMOutputModel, BaseModel

from ..redis.session import RSession

OutputSchemaType = TypeVar("OutputSchemaType", LLMOutputModel, AgentOutputSchemaBase)


class Tokens(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


async def get_mcp_servers(mcp_servers: dict[str, Any]):
    servers: list[MCPServer] = []

    for server_name, server_config in mcp_servers.items():
        url = server_config.get("url")
        if url:
            server = MCPServerSse(
                params=MCPServerSseParams(
                    url=url,
                    headers=server_config.get("headers", {}) or {},
                    timeout=200.0,
                    sse_read_timeout=300.0,
                ),
                cache_tools_list=True,
                name=server_name,
                client_session_timeout_seconds=300.0,
            )

        else:
            continue

        # else:
        #     server = MCPServerStdio(name=server_name, params=server_config)

        await server.connect()
        servers.append(server)

    return servers


async def close_mcp_servers(mcp_servers: list[MCPServer]):
    for server in reversed(mcp_servers):
        await server.cleanup()


class Agent:
    """
    基于 openai-agents 封装的 Agent.

    - 支持 Agent 多轮会话 session (每次会话用不同的 session 或统一用 Agent 创建时的 session 实现关联对话).
    - 支持 Agent 每次 run 的时候生成不同的结构化对象.
    """

    def __init__(
        self,
        name: str,
        instructions: (
            str
            | Callable[
                [RunContextWrapper[TContext], BasicAgent[TContext]],
                MaybeAwaitable[str],
            ]
            | None
        ) = None,
        model: Model | None | str = None,
        session: RSession | None = None,
        ctx: Any | None = None,
        **kwargs: Any,
    ):
        self.name = name
        self.instructions = instructions
        self.model = model
        self.session = session
        self.ctx = ctx

        self.agent = BasicAgent[ctx](
            name=self.name,
            instructions=self.instructions,
            model=self.model,
            **kwargs,
        )

    def run_streamed(
        self,
        input: str | list[TResponseInputItem],
        session: RSession | None = None,
        output_type: type[OutputSchemaType] | None = None,
        **kwargs: Any,
    ) -> RunResultStreaming:
        agent = self.agent.clone(output_type=output_type, **kwargs)
        return Runner.run_streamed(
            agent, input=input, session=session or self.session, context=self.ctx
        )

    async def run(
        self,
        input: str | list[dict[str, Any]],
        session: RSession | None = None,
        output_type: type[OutputSchemaType] | None = None,
        **kwargs: Any,
    ) -> tuple[RunResult | OutputSchemaType, Tokens]:
        agent = self.agent.clone(output_type=output_type, **kwargs)
        run_result = await Runner.run(
            agent,
            input=input,  # pyright: ignore[reportArgumentType]
            session=session or self.session,
            context=self.ctx,
        )

        tokens = Tokens(
            input_tokens=run_result.context_wrapper.usage.input_tokens or 0,
            output_tokens=run_result.context_wrapper.usage.output_tokens or 0,
            cached_tokens=run_result.context_wrapper.usage.input_tokens_details.cached_tokens
            or 0,
        )

        if output_type is not None:
            return run_result.final_output_as(output_type), tokens

        return run_result, tokens
