"""Golden dataset của Agent Architect (docs/agent-architect.md mục 2, 11).

Gọi Claude thật → tốn tiền, chỉ chạy khi:
    AIAGENT_GOLDEN=1 AIAGENT_ANTHROPIC_API_KEY=... pytest -m golden -s
Chạy lại mỗi khi đổi prompt/model của Architect; ngưỡng dưới đây chặn hồi quy.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from aiagent.bootstrap.container import PluginCatalogProvider
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.modules.agents.domain import AgentSpec
from aiagent.modules.builder.domain import UsageMeter
from aiagent.modules.builder.infrastructure.llm_brain import LlmArchitectBrain
from aiagent.modules.runtime.application import ModelRouter
from aiagent.modules.runtime.domain import estimate_cost
from aiagent.modules.runtime.infrastructure.anthropic_model import AnthropicChatModel
from tests.fakes import principal

pytestmark = pytest.mark.golden

CASES = json.loads((Path(__file__).parent / "architect_cases.json").read_text(encoding="utf-8"))
THRESHOLDS = {"valid": 0.95, "skills": 0.85, "avg_questions": 2.0, "rules": 0.85}


@pytest.fixture(scope="module")
def brain_and_catalog() -> tuple[LlmArchitectBrain, Any]:
    key = os.environ.get("AIAGENT_ANTHROPIC_API_KEY")
    if os.environ.get("AIAGENT_GOLDEN") != "1" or not key:
        pytest.skip("Golden eval tắt (đặt AIAGENT_GOLDEN=1 và AIAGENT_ANTHROPIC_API_KEY)")
    model = AnthropicChatModel(key)
    plugins = PluginRegistry()
    plugins.load_all()
    catalog = PluginCatalogProvider(plugins, ModelRouter({"anthropic": model})).catalog_for(
        principal("golden", "builder")
    )
    return LlmArchitectBrain(model), catalog


async def _run_case(
    brain: LlmArchitectBrain, catalog: Any, case: dict[str, Any], meter: UsageMeter
) -> dict[str, Any]:
    expect = case["expect"]
    first = await brain.understand([case["description"]], True, meter)
    brief = (
        first
        if not first.questions
        else await brain.understand(
            [case["description"], "Trả lời: hãy tự giả định hợp lý"], False, meter
        )
    )
    plan = await brain.plan(brief, catalog, meter)
    draft = await brain.compose(brief, plan, [], meter)
    try:
        AgentSpec.model_validate(
            {
                "name": draft.name[:80] or "x",
                "description": draft.description[:1000],
                "rules": [r[:500] for r in draft.rules][:50],
                "skills": [
                    {"skill_id": s.skill_id, "allowed_tools": s.allowed_tools} for s in plan.skills
                ],
            }
        )
        valid = True
    except ValueError:
        valid = False
    skills = sorted(s.skill_id for s in plan.skills)
    rules_text = " ".join(draft.rules).casefold()
    checks = {
        "valid": valid,
        "skills": skills == sorted(expect.get("skills", [])),
        "rules": all(k.casefold() in rules_text for k in expect.get("rules_contain", [])),
        "questions_ok": expect.get("min_questions", 0)
        <= len(first.questions)
        <= expect.get("max_questions", 3),
        "audience": expect.get("audience") in (None, brief.audience),
        "missing": all(
            k in {m.kind for m in plan.missing} for k in expect.get("missing_kinds", [])
        ),
    }
    return {"id": case["id"], "questions": len(first.questions), "skills": skills, **checks}


async def test_architect_golden(brain_and_catalog: tuple[LlmArchitectBrain, Any]) -> None:
    brain, catalog = brain_and_catalog
    meter = UsageMeter()
    semaphore = asyncio.Semaphore(4)

    async def guarded(case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _run_case(brain, catalog, case, meter)

    results = await asyncio.gather(*(guarded(c) for c in CASES))
    n = len(results)
    metrics = {
        "valid": sum(r["valid"] for r in results) / n,
        "skills": sum(r["skills"] for r in results) / n,
        "rules": sum(r["rules"] for r in results) / n,
        "avg_questions": sum(r["questions"] for r in results) / n,
        "questions_ok": sum(r["questions_ok"] for r in results) / n,
        "audience": sum(r["audience"] for r in results) / n,
        "missing": sum(r["missing"] for r in results) / n,
    }
    print("\nGolden Architect:", json.dumps(metrics, indent=2))  # noqa: T201
    print("Chi phí ước tính:", estimate_cost("claude-opus-5-5", meter.usage), "USD")  # noqa: T201
    for r in results:
        if not all(r[k] for k in ("valid", "skills", "rules", "questions_ok", "audience")):
            print("  ✗", r)  # noqa: T201

    assert metrics["valid"] >= THRESHOLDS["valid"]
    assert metrics["skills"] >= THRESHOLDS["skills"]
    assert metrics["rules"] >= THRESHOLDS["rules"]
    assert metrics["avg_questions"] <= THRESHOLDS["avg_questions"]
