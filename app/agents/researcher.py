from __future__ import annotations

import re

from app.agents.base import BaseAgent
from app.schemas.state import ResearchNote, RunContext
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.scraper import PageFetcher
from app.tools.thread_memory import format_run_context
from app.tools.web_search import WebSearchTool


class ResearcherAgent(BaseAgent):
    def __init__(
        self,
        search_tool: WebSearchTool,
        page_fetcher: PageFetcher,
        llm_client: OpenAIResponsesClient | None = None,
    ):
        super().__init__(name="researcher", role="specialist_research")
        self.search_tool = search_tool
        self.page_fetcher = page_fetcher
        self.llm_client = llm_client

    def research_company(
        self,
        company: str,
        goal: str,
        criteria: list[str],
        run_context: RunContext | None = None,
        max_sources: int = 4,
    ) -> ResearchNote:
        query = f"{company} {' '.join(criteria)} {goal}".strip()
        sources = self.search_tool.search_web(query=query, max_results=max_sources * 2)
        sources = self._filter_sources(company=company, sources=sources, max_sources=max_sources)

        facts: list[str] = []
        enriched_sources = []
        for source in sources:
            snippet = source.snippet
            if not snippet and source.url:
                try:
                    snippet = self.page_fetcher.fetch_page(source.url, max_chars=600)
                except Exception:
                    snippet = ""

            fact = self._extract_fact(company=company, text=snippet, criteria=criteria)
            if fact:
                facts.append(fact)

            enriched_sources.append(source.model_copy(update={"snippet": snippet[:240]}))

        if not facts:
            facts = ["Insufficient high-confidence data from automated search; verify manually."]

        source_quality = sum(1 for source in enriched_sources if source.url.startswith("http"))
        confidence = min(0.95, round(0.35 + 0.1 * source_quality + 0.08 * len(facts), 2))

        if self.llm_client and self.llm_client.enabled:
            llm_note = self._research_with_llm(
                company=company,
                query=query,
                criteria=criteria,
                run_context=run_context,
                sources=enriched_sources,
            )
            if llm_note is not None:
                return llm_note

        return ResearchNote(
            company=company,
            question=query,
            facts=facts,
            sources=enriched_sources,
            confidence=confidence,
        )

    def _research_with_llm(
        self,
        *,
        company: str,
        query: str,
        criteria: list[str],
        run_context: RunContext | None,
        sources,
    ) -> ResearchNote | None:
        try:
            payload = self.llm_client.generate_json(
                system_prompt=(
                    "You are a specialist research worker in a multi-agent team. "
                    "Use only the provided source snippets. Return structured JSON."
                ),
                user_prompt=(
                    "Return JSON with this structure exactly:\n"
                    "{"
                    "\"company\":\"...\","
                    "\"question\":\"...\","
                    "\"facts\":[\"...\"],"
                    "\"sources\":[{\"title\":\"...\",\"url\":\"...\",\"snippet\":\"...\"}],"
                    "\"confidence\":0.0"
                    "}\n\n"
                    f"Assigned company: {company}\n"
                    f"Criteria: {criteria}\n"
                    f"Conversation context:\n{format_run_context(run_context)}\n\n"
                    f"Source evidence: {[source.model_dump(mode='json') for source in sources]}"
                ),
                max_output_tokens=1100,
            )
            note = ResearchNote.model_validate(payload)
            if note.company != company:
                return None
            return note
        except (OpenAIResponsesError, ValueError):
            return None

    def _extract_fact(self, company: str, text: str, criteria: list[str]) -> str:
        if not text:
            return ""

        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if sentence.strip()
        ]
        if not sentences:
            return ""

        lowered_criteria = [item.lower() for item in criteria]
        company_tokens = self._company_tokens(company)
        for sentence in sentences:
            sentence_lower = sentence.lower()
            if any(token in sentence_lower for token in company_tokens) and any(
                criterion in sentence_lower for criterion in lowered_criteria
            ):
                return sentence[:280]

        for sentence in sentences:
            sentence_lower = sentence.lower()
            if any(token in sentence_lower for token in company_tokens):
                return sentence[:280]

        return sentences[0][:280]

    def _filter_sources(self, company: str, sources, max_sources: int):
        company_tokens = self._company_tokens(company)
        scored = []
        for source in sources:
            title_text = source.title.lower()
            url_text = source.url.lower()
            snippet_text = source.snippet.lower()

            score = 0
            for token in company_tokens:
                if token in title_text:
                    score += 4
                if token in url_text:
                    score += 3
                if token in snippet_text:
                    score += 1
            if score > 0:
                scored.append((score, source))

        if scored:
            ranked = [source for _, source in sorted(scored, key=lambda item: item[0], reverse=True)]
            unique_ranked = []
            seen = set()
            for source in ranked:
                if source.url in seen:
                    continue
                seen.add(source.url)
                unique_ranked.append(source)
            if len(unique_ranked) >= max_sources:
                return unique_ranked[:max_sources]

            remainder = [source for source in sources if source.url not in seen]
            return unique_ranked + remainder[: max_sources - len(unique_ranked)]

        return sources[:max_sources]

    def _company_tokens(self, company: str) -> list[str]:
        tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", company)]
        ignored = {
            "the",
            "and",
            "for",
            "international",
            "global",
            "group",
            "company",
            "co",
            "inc",
            "llc",
            "ltd",
            "corp",
            "corporation",
            "technologies",
            "technology",
        }
        filtered = [token for token in tokens if len(token) > 2 and token not in ignored]
        return filtered or [company.lower()]
