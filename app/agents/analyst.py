from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.state import AnalysisResult, CompanyComparison, ResearchNote


class AnalystAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="analyst", role="specialist_analysis")

    def analyze(self, notes: list[ResearchNote], criteria: list[str]) -> AnalysisResult:
        comparisons: list[CompanyComparison] = []

        for note in notes:
            merged_text = " ".join(note.facts + [source.snippet for source in note.sources]).lower()
            cost_score = self._score_dimension(merged_text, "cost")
            scalability_score = self._score_dimension(merged_text, "scalability")
            technology_score = self._score_dimension(merged_text, "technology")

            comparisons.append(
                CompanyComparison(
                    company=note.company,
                    cost_score=cost_score,
                    scalability_score=scalability_score,
                    technology_score=technology_score,
                    rationale=(
                        f"Scores estimated from {len(note.facts)} extracted facts and "
                        f"{len(note.sources)} collected sources."
                    ),
                )
            )

        ranked = sorted(
            comparisons,
            key=lambda item: item.cost_score + item.scalability_score + item.technology_score,
            reverse=True,
        )

        takeaways = []
        if ranked:
            leader = ranked[0]
            takeaways.append(
                f"{leader.company} has the highest composite score in this automated pass."
            )
        takeaways.append(
            "Scores are heuristic and should be validated against primary financial and technical documents."
        )

        return AnalysisResult(
            criteria=criteria,
            comparisons=comparisons,
            key_takeaways=takeaways,
        )

    def _score_dimension(self, text: str, dimension: str) -> int:
        keyword_sets = {
            "cost": ["cost", "price", "funding", "budget", "affordable", "economic"],
            "scalability": ["scale", "scalable", "expansion", "deployment", "global", "growth"],
            "technology": ["technology", "platform", "3d", "sensor", "ai", "automation"],
        }

        score = 2
        for keyword in keyword_sets.get(dimension, []):
            if keyword in text:
                score += 1

        return max(1, min(5, score))
