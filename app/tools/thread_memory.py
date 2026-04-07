from __future__ import annotations

from typing import Any

from app.schemas.state import ConversationTurn, RunContext
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError


def build_run_context(
    *,
    thread_summary: str,
    previous_messages: list[dict[str, Any]],
    current_message: str,
    recent_limit: int,
) -> RunContext:
    recent_messages = [
        ConversationTurn(role=message["role"], content=message["content"])
        for message in previous_messages[-recent_limit:]
    ]
    return RunContext(
        current_message=current_message,
        thread_summary=thread_summary or "",
        recent_messages=recent_messages,
    )


def format_run_context(run_context: RunContext | None) -> str:
    if run_context is None:
        return "No prior thread context."

    recent_turns = "\n".join(
        f"- {turn.role}: {turn.content}" for turn in run_context.recent_messages
    ) or "- None"

    summary = run_context.thread_summary or "None"
    return (
        f"Thread summary:\n{summary}\n\n"
        f"Recent turns:\n{recent_turns}\n\n"
        f"Current user message:\n{run_context.current_message}"
    )


def refresh_thread_summary(
    *,
    llm_client: OpenAIResponsesClient | None,
    previous_summary: str,
    run_context: RunContext,
    assistant_output: str,
) -> str:
    if llm_client and llm_client.enabled:
        summary = _refresh_with_llm(
            llm_client=llm_client,
            previous_summary=previous_summary,
            run_context=run_context,
            assistant_output=assistant_output,
        )
        if summary:
            return summary

    recent_turns = " | ".join(
        f"{turn.role}: {turn.content}" for turn in run_context.recent_messages[-3:]
    )
    summary_parts = [
        previous_summary.strip(),
        recent_turns,
        f"user: {run_context.current_message}",
        f"assistant: {assistant_output[:400]}",
    ]
    compact = " ".join(part for part in summary_parts if part)
    return compact[:1200]


def _refresh_with_llm(
    *,
    llm_client: OpenAIResponsesClient,
    previous_summary: str,
    run_context: RunContext,
    assistant_output: str,
) -> str | None:
    try:
        return llm_client.generate_text(
            system_prompt=(
                "You maintain compact thread memory for a multi-agent desktop app. "
                "Return a concise plain-text summary of persistent context, prior decisions, "
                "entities, and follow-up intent. Keep it under 180 words."
            ),
            user_prompt=(
                f"Previous summary:\n{previous_summary or 'None'}\n\n"
                f"{format_run_context(run_context)}\n\n"
                f"Assistant output:\n{assistant_output}\n"
            ),
            max_output_tokens=220,
        )
    except OpenAIResponsesError:
        return None
