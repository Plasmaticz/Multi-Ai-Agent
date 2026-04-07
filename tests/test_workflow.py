import time

from app.config import Settings
from app.schemas.state import Source
from app.tools.web_search import StubSearchProvider
from app.workflows.run_crew import CrewRunner


def test_workflow_completes_with_required_sections():
    provider = StubSearchProvider(
        catalog={
            "Archireef": [
                Source(
                    title="Archireef Tech Overview",
                    url="https://example.com/archireef-tech",
                    snippet="Archireef uses 3D printed reef tiles and focuses on scalable deployment.",
                ),
                Source(
                    title="Archireef Funding",
                    url="https://example.com/archireef-funding",
                    snippet="Archireef reports project cost planning and long-term restoration economics.",
                ),
            ],
            "Coral Vita": [
                Source(
                    title="Coral Vita Model",
                    url="https://example.com/coralvita-model",
                    snippet="Coral Vita grows climate-resilient corals and expands across coastal markets.",
                ),
                Source(
                    title="Coral Vita Cost",
                    url="https://example.com/coralvita-cost",
                    snippet="Coral Vita describes cost structure and global scale partnerships.",
                ),
            ],
            "SECORE International": [
                Source(
                    title="SECORE Technology",
                    url="https://example.com/secore-tech",
                    snippet="SECORE International develops coral propagation technology and restoration methods.",
                ),
                Source(
                    title="SECORE Scale",
                    url="https://example.com/secore-scale",
                    snippet="SECORE International coordinates scalable reef restoration pilots.",
                ),
            ],
        }
    )

    settings = Settings(
        openai_api_key="",
        max_review_loops=1,
        min_sources_per_company=2,
        default_companies="Archireef,Coral Vita,SECORE International",
    )
    runner = CrewRunner(settings=settings, search_provider=provider)

    state = runner.run(
        goal="Compare 3 coral restoration startups by cost, scalability, and technology"
    )

    assert state.status == "complete"
    assert len(state.research_notes) == 3
    assert len(state.review_notes) >= 1
    assert state.metadata.get("llm_enabled") is False
    assert all(task["status"] == "completed" for task in state.tasks)

    output = state.final_output.lower()
    assert "executive summary" in output
    assert "comparison table" in output
    assert "sources" in output
    assert "http" in output


def test_research_runs_concurrently():
    settings = Settings(
        openai_api_key="",
        max_review_loops=0,
        min_sources_per_company=1,
        max_concurrent_research=3,
        default_companies="Alpha Labs,Beta Reef,Gamma Marine",
    )
    runner = CrewRunner(settings=settings, search_provider=StubSearchProvider())

    def slow_research(company: str, goal: str, criteria: list[str], max_sources: int = 4):
        time.sleep(0.25)
        return runner._build_failed_research_note(company=company, goal=goal, error="synthetic")

    runner.researcher.research_company = slow_research

    started_at = time.perf_counter()
    state = runner.run(
        goal="Compare three companies",
        companies=["Alpha Labs", "Beta Reef", "Gamma Marine"],
    )
    elapsed = time.perf_counter() - started_at

    assert state.status == "needs_human_review"
    assert len(state.research_notes) == 3
    assert state.metadata.get("research_concurrency") == 3
    assert elapsed < 0.55
