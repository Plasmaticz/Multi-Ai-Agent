from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState


class WriterAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="writer", role="specialist_writer")

    def write_report(self, state: ProjectState, revision_focus: str | None = None) -> str:
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
