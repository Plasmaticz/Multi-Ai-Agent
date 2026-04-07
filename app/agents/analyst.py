from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import AnalysisResult, CompanyComparison, ResearchNote, RunContext
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="analyst", role="specialist_analysis")
        self.llm_client = llm_client

    def analyze(
        self,
        notes: list[ResearchNote],
        criteria: list[str],
        run_context: RunContext | None = None,
    ) -> AnalysisResult:
        if self.llm_client and self.llm_client.enabled:
            llm_result = self._analyze_with_llm(notes=notes, criteria=criteria, run_context=run_context)
            if llm_result is not None:
                return llm_result

        return self._analyze_heuristic(notes=notes, criteria=criteria)

    def _analyze_with_llm(
        self,
        notes: list[ResearchNote],
        criteria: list[str],
        run_context: RunContext | None,
    ) -> AnalysisResult | None:
        if not notes:
            return AnalysisResult(criteria=criteria, comparisons=[], key_takeaways=[])

        system_prompt = (
            "You are an analyst agent. Build objective comparisons from research notes. "
            "Use only provided evidence. Keep scores between 1 and 5."
        )

        simplified_notes = []
        for note in notes:
            simplified_notes.append(
                {
                    "company": note.company,
                    "facts": note.facts,
                    "sources": [
                        {"title": source.title, "url": source.url, "snippet": source.snippet}
                        for source in note.sources
                    ],
                }
            )

        user_prompt = (
            "Analyze the notes and return this JSON structure exactly:\n"
            "{"
            "\"criteria\": [\"cost\", \"scalability\", \"technology\"],"
            "\"comparisons\": ["
            "{\"company\": \"...\", \"cost_score\": 1-5, \"scalability_score\": 1-5, "
            "\"technology_score\": 1-5, \"rationale\": \"...\"}"
            "],"
            "\"key_takeaways\": [\"...\", \"...\"]"
            "}\n\n"
            f"Required criteria: {criteria}\n"
            f"Conversation context:\n{format_run_context(run_context)}\n\n"
            f"Research notes: {json.dumps(simplified_notes)}"
        )

        try:
            payload = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=1200,
            )
            result = AnalysisResult.model_validate(payload)
            known_companies = {note.company for note in notes}
            if any(item.company not in known_companies for item in result.comparisons):
                return None
            return result
        except (OpenAIResponsesError, ValueError):
            logger.exception("LLM analysis failed; falling back to heuristic analysis.")
            return None

    def _analyze_heuristic(self, notes: list[ResearchNote], criteria: list[str]) -> AnalysisResult:
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
