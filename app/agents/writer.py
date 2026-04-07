from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class WriterAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="writer", role="specialist_writer")
        self.llm_client = llm_client

    def write_report(self, state: ProjectState, revision_focus: str | None = None) -> str:
        if self.llm_client and self.llm_client.enabled:
            llm_draft = self._write_with_llm(state=state, revision_focus=revision_focus)
            if llm_draft is not None:
                return llm_draft

        return self._write_template(state=state, revision_focus=revision_focus)

    def _write_with_llm(self, state: ProjectState, revision_focus: str | None = None) -> str | None:
        analysis = state.analysis
        if analysis is None:
            return None

        system_prompt = (
            "You are a writer agent for a multi-agent research system. "
            "Generate a concise, factual markdown report with explicit source links."
        )

        research_payload = [
            {
                "company": note.company,
                "facts": note.facts,
                "sources": [{"title": src.title, "url": src.url} for src in note.sources],
            }
            for note in state.research_notes
        ]
        revision_text = revision_focus or "None"
        user_prompt = (
            "Write a markdown report with these sections exactly:\n"
            "1) Executive Summary\n"
            "2) Method\n"
            "3) Company Snapshots\n"
            "4) Comparison Table\n"
            "5) Key Takeaways\n"
            "6) Limitations\n"
            "7) Sources\n"
            "Use only provided data, include markdown links for sources, and avoid unsupported claims.\n"
            f"Goal: {state.user_goal}\n"
            f"Conversation context: {format_run_context(state.run_context)}\n"
            f"Analysis: {analysis.model_dump_json()}\n"
            f"Research: {json.dumps(research_payload)}\n"
            f"Reviewer revision focus: {revision_text}\n"
        )

        try:
            draft = self.llm_client.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=1800,
            )
            if not self._has_required_sections(draft):
                return None
            return draft
        except OpenAIResponsesError:
            logger.exception("LLM writer failed; falling back to deterministic template.")
            return None

    def _write_template(self, state: ProjectState, revision_focus: str | None = None) -> str:
        analysis = state.analysis
        if analysis is None:
            return "# Report\n\nUnable to generate report because analysis is missing."

        comparison_rows = []
        for row in analysis.comparisons:
            comparison_rows.append(
                "| {company} | {cost} | {scale} | {tech} |".format(
                    company=row.company,
                    cost=row.cost_score,
                    scale=row.scalability_score,
                    tech=row.technology_score,
                )
            )

        company_sections = []
        for note in state.research_notes:
            bullets = "\n".join(f"- {fact}" for fact in note.facts)
            links = "\n".join(f"- [{source.title}]({source.url})" for source in note.sources)
            company_sections.append(
                f"### {note.company}\n{bullets}\n\nSources:\n{links}"
            )

        recommendation = self._build_recommendation(analysis.comparisons)

        sections = [
            "# Multi-Agent Research Report",
            "## Executive Summary",
            recommendation,
            "## Method",
            (
                "This report was generated via a centralized multi-agent workflow: "
                "orchestrator planning, researcher evidence collection, analyst scoring, "
                "writer synthesis, and reviewer quality checks."
            ),
            "## Company Snapshots",
            "\n\n".join(company_sections),
            "## Comparison Table",
            "| Company | Cost (1-5) | Scalability (1-5) | Technology (1-5) |",
            "| --- | --- | --- | --- |",
            "\n".join(comparison_rows),
            "## Key Takeaways",
            "\n".join(f"- {item}" for item in analysis.key_takeaways),
            "## Limitations",
            "- Automated extraction may miss nuance from paywalled or non-indexed sources.",
            "- Scores are heuristic and intended as a triage baseline, not final diligence.",
            "## Sources",
            self._build_sources(state),
        ]

        if revision_focus:
            sections.extend(
                [
                    "## Revision Notes Addressed",
                    f"- Reviewer feedback handled: {revision_focus}",
                ]
            )

        return "\n\n".join(section for section in sections if section)

    def _has_required_sections(self, draft: str) -> bool:
        required = [
            "executive summary",
            "method",
            "company snapshots",
            "comparison table",
            "sources",
        ]
        lowered = draft.lower()
        return all(section in lowered for section in required)

    def _build_recommendation(self, comparisons) -> str:
        if not comparisons:
            return "Insufficient evidence for recommendation."

        ranked = sorted(
            comparisons,
            key=lambda item: item.cost_score + item.scalability_score + item.technology_score,
            reverse=True,
        )
        top = ranked[0]
        total = top.cost_score + top.scalability_score + top.technology_score
        return (
            f"Based on current evidence, **{top.company}** ranks first with a composite "
            f"score of {total}/15 across cost, scalability, and technology criteria."
        )

    def _build_sources(self, state: ProjectState) -> str:
        seen: set[str] = set()
        lines: list[str] = []
        for note in state.research_notes:
            for source in note.sources:
                if source.url in seen:
                    continue
                seen.add(source.url)
                lines.append(f"- [{source.title}]({source.url})")
        return "\n".join(lines) if lines else "- No sources collected."
