from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookMatcher,
    ResultMessage,
    TextBlock,
)


@dataclass
class ClaudeFilesystemConfig:
    """Configuration for which paths the Claude Agent may read or edit.

    NOTE: This is currently a declarative configuration only. Enforcement will
    be wired into Claude Agent SDK hooks and tool options in a follow-up
    change.
    """

    read_only_roots: list[Path]
    editable_files: list[Path]


@dataclass
class ClaudeAgentRuntimeConfig:
    """Runtime configuration for using Claude Code as the step agent backend."""

    cwd: Path
    allowed_tools: list[str]
    permission_mode: str = "acceptEdits"
    filesystem: ClaudeFilesystemConfig | None = None
    usage_sink: list[dict[str, Any]] | None = None


@dataclass
class ClaudeCallResult:
    """Text response plus optional SDK usage/cost metadata for one query."""

    text: str
    usage: dict[str, Any] = field(default_factory=dict)


def summarize_usage_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-call usage records into a run-level cost summary."""
    total_cost = 0.0
    cost_known = False
    usage_totals: dict[str, float] = {}
    for record in records:
        cost = record.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)
            cost_known = True
        usage = record.get("usage") or {}
        if isinstance(usage, dict):
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    usage_totals[key] = usage_totals.get(key, 0.0) + float(value)
    return {
        "calls": len(records),
        "total_cost_usd": total_cost if cost_known else None,
        "usage_totals": usage_totals,
        "note": (
            "total_cost_usd is the Claude Agent SDK client-side estimate "
            "(often Anthropic-rate equivalent). With FireConnect/Fireworks routing, "
            "treat it as a relative estimate unless you apply provider-specific pricing."
        ),
        "calls_detail": records,
    }


def _usage_record_from_result(
    message: ResultMessage,
    *,
    step_tag: str,
    iteration: int,
) -> dict[str, Any]:
    """Normalize ResultMessage cost/usage fields for trajectory persistence."""
    return {
        "step_tag": step_tag,
        "iteration": iteration,
        "total_cost_usd": message.total_cost_usd,
        "usage": message.usage,
        "model_usage": message.model_usage,
        "duration_ms": message.duration_ms,
        "duration_api_ms": message.duration_api_ms,
        "num_turns": message.num_turns,
        "session_id": message.session_id,
        "is_error": message.is_error,
        "subtype": message.subtype,
    }


async def _async_run_claude(  # noqa: C901
    *,
    prompt: str,
    system_prompt: str | None,
    config: ClaudeAgentRuntimeConfig,
    step_tag: str,
    iteration: int,
) -> ClaudeCallResult:
    """Execute a single Claude Code call and return text plus usage metadata."""
    hooks: dict[str, list[HookMatcher]] | None = None
    if config.filesystem is not None:
        filesystem = config.filesystem
        cwd = config.cwd

        async def filesystem_guard(
            input_data: dict[str, Any],
            tool_use_id: str | None,
            context: HookContext,
        ) -> dict[str, Any]:
            """PreToolUse hook that enforces write restrictions based on filesystem config."""
            tool_name = input_data.get("tool_name")
            if tool_name != "Write":
                return {}
            tool_input = input_data.get("tool_input") or {}
            file_path = tool_input.get("file_path")
            if not file_path:
                return {}

            target = Path(str(file_path))
            if not target.is_absolute():
                target = (cwd / target).resolve()

            editable_set = {p.resolve() for p in filesystem.editable_files}
            if target not in editable_set:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Write to '{target}' is not permitted. "
                            f"Editable files are limited to: {', '.join(str(p) for p in sorted(editable_set))}"
                        ),
                    }
                }

            return {}

        hooks = {
            "PreToolUse": [
                HookMatcher(hooks=[filesystem_guard]),  # type: ignore[list-item]
            ]
        }

    options_kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "permission_mode": config.permission_mode,
        "cwd": str(config.cwd),
        "allowed_tools": config.allowed_tools,
    }
    if hooks is not None:
        options_kwargs["hooks"] = hooks

    options = ClaudeAgentOptions(**options_kwargs)

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt, session_id=f"{step_tag}-{iteration}")
        text_chunks: list[str] = []
        usage_record: dict[str, Any] = {}

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_chunks.append(block.text)
            elif isinstance(message, ResultMessage):
                usage_record = _usage_record_from_result(
                    message,
                    step_tag=step_tag,
                    iteration=iteration,
                )

        if usage_record and config.usage_sink is not None:
            config.usage_sink.append(usage_record)

        return ClaudeCallResult(text="".join(text_chunks).strip(), usage=usage_record)

    # Fallback to satisfy static analyzers; normal execution should always
    # return from inside the context block above.
    return ClaudeCallResult(text="")


def _conversation_to_prompt(
    conversation: list[dict[str, str]],
    *,
    step_tag: str,
    iteration: int,
) -> str:
    """Render a role-based conversation into a single textual prompt.

    The Claude Agent SDK already accepts a separate `system_prompt` via
    `ClaudeAgentOptions`, so this helper intentionally omits system-role
    messages and only inlines user/assistant turns built from the
    user-facing prompt templates in `prompts.py`.
    """
    parts: list[str] = [f"[STEP] {step_tag} | iteration {iteration}"]
    for message in conversation:
        role = message.get("role", "user").upper()
        if role == "SYSTEM":
            continue
        content = message.get("content", "")
        parts.append(f"{role}:\n{content}")
    return "\n\n".join(parts)


def run_claude_conversation(
    conversation: list[dict[str, str]],
    *,
    system_prompt: str | None,
    config: ClaudeAgentRuntimeConfig,
    step_tag: str,
    iteration: int,
) -> ClaudeCallResult:
    """Synchronous wrapper to run Claude Code for a given step conversation."""
    prompt = _conversation_to_prompt(conversation, step_tag=step_tag, iteration=iteration)
    return asyncio.run(
        _async_run_claude(
            prompt=prompt,
            system_prompt=system_prompt,
            config=config,
            step_tag=step_tag,
            iteration=iteration,
        )
    )
