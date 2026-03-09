from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState, ReviewNote


class ReviewerAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="reviewer", role="critic")

    def review(
        self,
        state: ProjectState,
        draft: str,
        min_sources_per_company: int,
    ) -> ReviewNote:
        issues: list[str] = []

        for required_section in [
            "Executive Summary",
            "Method",
            "Company Snapshots",
            "Comparison Table",
            "Sources",
        ]:
            if required_section.lower() not in draft.lower():
                issues.append(f"Missing required section: {required_section}.")

        for note in state.research_notes:
            if len(note.sources) < min_sources_per_company:
                issues.append(
                    f"{note.company} has fewer than {min_sources_per_company} sources."
                )

        if "http" not in draft:
            issues.append("Draft does not include source links.")

        passed = len(issues) == 0
        confidence = 0.9 if passed else 0.55
        return ReviewNote(passed=passed, issues=issues, confidence=confidence)
