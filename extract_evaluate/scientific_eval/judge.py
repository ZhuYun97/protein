from __future__ import annotations

import json
from typing import Any, Dict, Optional


SYSTEM_PROMPT = """You are an expert evaluator for scientific literature extraction results.

Evaluate exactly one extraction unit per request: either one node or one edge.
Use only the evidence included in the request as the factual source.
For nodes, judge whether the node type, name, description, and properties are supported by the node evidence. Do not judge the node id.
For edges, judge whether the relationship is supported by the edge evidence. The source_node and target_node fields provide only endpoint context; do not treat them as additional evidence.

Scoring rules:
- 5: Fully supported by the evidence, precise, and no material unsupported claim.
- 4: Mostly supported; only minor wording issues or harmless omissions.
- 3: Partially supported; the core claim is present, but some important details are unsupported, vague, or incomplete.
- 2: Weakly supported; major details are unsupported or the unit overstates the evidence.
- 1: Contradicted by the evidence, hallucinated, or the evidence is empty/irrelevant.

Return valid JSON only. Do not include markdown.
"""


def build_user_prompt(task: Dict[str, Any]) -> str:
    payload: Dict[str, Any] = {
        "task": "Evaluate one graph extraction unit against its evidence.",
        "unit_type": task["unit_type"],
        "unit_id": task["unit_id"],
        "evidence": task["evidence"],
        "extracted_unit": task["payload"],
        "output_format": {
            "score": "integer from 1 to 5",
            "verdict": "short judgment",
            "supported_points": ["facts that are supported by the evidence"],
            "problems": ["unsupported, contradicted, or ambiguous claims"],
            "evidence_quality": "short note on whether the evidence is sufficient",
        },
    }
    if task["unit_type"] == "edge":
        payload["source_node"] = task.get("source_node", {})
        payload["target_node"] = task.get("target_node", {})
    return json.dumps(payload, ensure_ascii=False, indent=2)


class OpenAIJudge:
    def __init__(
        self,
        *,
        model: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "Missing OpenAI API key. Pass --api-key or set the OPENAI_API_KEY environment variable."
            )
        try:
            from openai import AsyncOpenAI, BadRequestError
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: openai. Install it with `pip install -e .` or `pip install openai`."
            ) from exc

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**client_kwargs)
        self.model = model
        self.bad_request_error = BadRequestError

    async def evaluate_unit(self, task: Dict[str, Any]) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(task)},
        ]
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=messages,
            )
        except self.bad_request_error:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
            )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
