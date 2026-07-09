from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


PROFILE_SYSTEM_PROMPT = """You are an expert evaluator for scientific knowledge graph extraction quality.

You are NOT given the full paper. Use only the extracted graph and the unit-level evidence included in the request.
Do not search the web. Do not introduce external facts. Do not infer paper claims that are not visible in the graph or evidence.

Your task is to build an evidence-scoped graph profile that will later be used to evaluate scientific knowledge quality.
The profile is not a factuality report. It should identify the scientific topic, evidence-visible core claims, important scope qualifiers, and likely KG content implied by the provided graph and evidence.

Important limitation:
- Because the full paper is not provided, coverage must mean coverage of evidence-visible scientific content, not coverage of the entire paper.
- If the graph/evidence is sparse, set coverage_confidence to low and explain the limitation.

Return valid JSON only. Do not include markdown.
"""


UNIT_QUALITY_SYSTEM_PROMPT = """You are evaluating scientific knowledge quality for extracted nodes and edges from one scientific graph.

This is a second-stage evaluation after factuality checking. Do NOT primarily redo factuality evaluation.
Use factuality_score only as a gate when it is provided.

Use only:
- the graph unit
- its evidence
- source/target node context for edges
- the evidence-scoped graph profile
- optional target KG goal

Do not search the web. Do not introduce external facts. Do not assume access to the full paper.

For nodes, evaluate the scientific quality of the node type, name, description, and properties. Do not judge the node id.
For edges, evaluate the scientific quality of the relation type, direction, description, and properties. Source and target node names/types may be used to understand graph semantics.

Evaluate each unit using these dimensions:

1. scientific_value_score
5 = central or meaningful scientific knowledge, such as a gene-disease relation, variant, mechanism, experimental finding, method, dataset, therapeutic approach, or validated result.
3 = relevant but generic, incomplete, or only moderately useful.
1 = trivial, bibliographic, administrative, vague, or not meaningful scientific knowledge.

2. evidence_strength_score
5 = the provided evidence gives strong scientific support, such as explicit result, experimental observation, clinical observation, validated method, strong comparison, or clear measurement.
3 = moderately supported, narrow, preliminary, or based on limited evidence.
1 = weak, speculative, abstract-only, or not enough scientific basis in the provided evidence.

3. claim_evidence_alignment_score
5 = the extracted claim is proportional to the evidence and does not overstate it.
3 = mostly aligned but slightly broader or stronger than the evidence.
1 = clearly overstated, such as association expressed as causation, narrow experiment expressed as general fact, or preliminary evidence expressed as established conclusion.

4. scope_completeness_score
5 = preserves necessary scientific scope, such as mutation, population, cell type, model system, condition, dataset, metric, time, dose, or limitation.
3 = partially scoped but missing important qualifiers.
1 = too broad, ambiguous, or likely to mislead without missing context.

5. kg_reusability_score
5 = type, name, relation, direction, and properties are specific, normalized, useful, and easy to integrate into a scientific KG.
3 = usable but needs better type, relation, normalization, direction, or properties.
1 = vague, generic, redundant, poorly typed, or hard to integrate.

Apply these gates:
- If factuality_score is provided and <= 2, final_unit_quality_score must be 1.
- If factuality_score is 3, final_unit_quality_score must be <= 3.
- If claim_evidence_alignment_score <= 2, final_unit_quality_score must be <= 2.
- If scientific_value_score <= 2, final_unit_quality_score must be <= 2.
- If the unit is only bibliographic or administrative metadata, final_unit_quality_score must be <= 2 unless the target KG explicitly focuses on bibliographic metadata.

Final unit score:
5 = high-quality scientific knowledge, central, well-scoped, evidence-proportional, and KG-ready.
4 = good scientific knowledge with minor issues.
3 = usable but incomplete, generic, weakly scoped, or needing revision.
2 = low-quality scientific knowledge; downweight or revise heavily.
1 = discard.

Use issue_tags from this controlled list when applicable:
low_scientific_value, weak_evidence, overclaim, missing_scope, missing_uncertainty, generic_relation, wrong_relation_granularity, ambiguous_node_type, redundant_node, redundant_edge, relation_target_imprecise, missing_intermediate_concept, missing_core_finding, missing_limitation, poor_graph_structure, failed_factuality_gate

Return valid JSON only. Do not include markdown.
"""


GRAPH_QUALITY_SYSTEM_PROMPT = """You are evaluating the overall scientific quality of an extracted node-edge graph.

The graph may already have factuality scores at unit level. Your task is NOT to redo factuality evaluation.
Your task is to evaluate whether the extracted graph, as a whole, is a high-quality scientific knowledge representation of the content visible in the graph and evidence.

Use only:
- extracted graph
- evidence-scoped graph profile
- unit quality scores
- factuality scores if available
- unit-level evidence included in the graph
- optional target KG goal

Do not search the web. Do not use external facts. Do not assume access to the full paper.

Important limitation:
- Since full paper text is not provided, core_knowledge_coverage_score must evaluate coverage of evidence-visible core scientific content and graph-internal scientific structure, not coverage of the entire paper.
- If the evidence only covers a narrow excerpt, mention that coverage confidence is limited.

Evaluate the graph using these dimensions:

1. core_knowledge_coverage_score
Does the graph cover the evidence-visible core scientific claims, key entities, key mechanisms, important results, and important limitations?
5 = covers the evidence-visible core scientific content well.
3 = captures some important content but misses important findings, intermediate concepts, conditions, or limitations visible in the evidence.
1 = mostly misses the main evidence-visible scientific contribution.

2. scientific_value_density_score
What proportion of the graph consists of meaningful scientific knowledge rather than trivial, generic, bibliographic, or low-information nodes/edges?
5 = most units are scientifically meaningful.
3 = mixed; useful knowledge exists but there is noticeable low-value content.
1 = dominated by low-value, generic, or administrative information.

3. claim_calibration_score
Are claims across the graph proportional to the evidence?
5 = relations and descriptions preserve proper claim strength.
3 = some mild overgeneralization or missing caution.
1 = systematic overclaiming, such as association to causation or narrow result to universal conclusion.

4. scope_and_context_preservation_score
Does the graph preserve key scientific scope, including population, mutation, model, cell type, experimental system, condition, dataset, metric, time, dose, or limitation?
5 = key scope is well preserved.
3 = partially preserved but important qualifiers are missing.
1 = scope is mostly missing, making the graph misleading or too broad.

5. structural_consistency_score
Are node types, edge types, directions, descriptions, and properties consistent and semantically clear?
5 = graph structure is clean, specific, and reusable.
3 = usable but has some vague edge types, inconsistent typing, imprecise direction, or missing intermediate concepts.
1 = structurally noisy, vague, inconsistent, or hard to integrate.

6. redundancy_noise_control_score
Does the graph avoid duplicate nodes, redundant edges, isolated low-value nodes, and generic relations?
5 = low redundancy and low noise.
3 = some redundancy or noise.
1 = high redundancy or noise.

7. evidence_quality_distribution_score
Considering the evidence-scoped profile and unit scores, is the graph mostly composed of knowledge supported by strong or moderate scientific evidence?
5 = most important units are backed by strong evidence.
3 = mixed evidence quality.
1 = many important claims rely on weak, speculative, or poorly scoped evidence.

Final graph score:
5 = excellent scientific KG extraction: high-value, well-scoped, evidence-calibrated, evidence-visible core content covered, structurally reusable.
4 = good extraction with minor issues.
3 = usable but incomplete or requiring revision.
2 = low-quality extraction with major scientific or structural problems.
1 = poor extraction; mostly unusable as scientific KG.

Apply these caps:
- If core_knowledge_coverage_score <= 2, final_graph_quality_score must be <= 3.
- If scientific_value_density_score <= 2, final_graph_quality_score must be <= 2.
- If claim_calibration_score <= 2, final_graph_quality_score must be <= 2.
- If structural_consistency_score <= 2, final_graph_quality_score must be <= 3.
- If more than 30% of units have final_unit_quality_score <= 2, final_graph_quality_score must be <= 3.
- If more than 50% of units have final_unit_quality_score <= 2, final_graph_quality_score must be <= 2.

Return valid JSON only. Do not include markdown.
"""


def build_profile_user_prompt(
    *,
    graph: Dict[str, Any],
    target_kg_goal: Optional[str],
) -> str:
    payload: Dict[str, Any] = {
        "task": "Create an evidence-scoped profile for scientific KG quality evaluation.",
        "profile_scope": "Use only extracted nodes, edges, and their evidence. Full paper text is not provided.",
        "target_kg_goal": target_kg_goal or "",
        "graph": graph,
        "output_schema": {
            "profile_source": "graph_and_unit_evidence_only",
            "coverage_confidence": "high | medium | low",
            "paper_or_study_type_guess": "",
            "evidence_visible_topic": "",
            "evidence_quality_score": "integer from 1 to 5",
            "evidence_visible_core_claims": [
                {
                    "claim_id": "C1",
                    "claim": "",
                    "evidence_strength_score": "integer from 1 to 5",
                    "required_scope": [],
                    "important_entities": [],
                    "important_relations": [],
                    "quality_notes": "",
                }
            ],
            "major_limitations_from_available_evidence": [],
            "expected_kg_content": {
                "important_node_types": [],
                "important_edge_types": [],
                "must_capture_findings": [],
                "must_capture_scope": [],
                "optional_content": [],
            },
            "rationale": "",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_unit_quality_user_prompt(
    *,
    units: List[Dict[str, Any]],
    evidence_graph_profile: Dict[str, Any],
    target_kg_goal: Optional[str],
) -> str:
    payload: Dict[str, Any] = {
        "task": "Evaluate scientific knowledge quality for a batch of graph units.",
        "target_kg_goal": target_kg_goal or "",
        "evidence_graph_profile": evidence_graph_profile,
        "units": units,
        "output_schema": {
            "unit_scores": [
                {
                    "unit_id": "",
                    "unit_kind": "node | edge",
                    "scientific_value_score": "integer from 1 to 5",
                    "evidence_strength_score": "integer from 1 to 5",
                    "claim_evidence_alignment_score": "integer from 1 to 5",
                    "scope_completeness_score": "integer from 1 to 5",
                    "kg_reusability_score": "integer from 1 to 5",
                    "final_unit_quality_score": "integer from 1 to 5",
                    "decision": "keep | keep_with_revision | downweight | discard",
                    "issue_tags": [],
                    "quality_issues": [],
                    "recommended_revision": "",
                    "rationale": "",
                }
            ],
            "batch_summary": {
                "high_quality_count": 0,
                "medium_quality_count": 0,
                "low_quality_count": 0,
                "discard_count": 0,
                "common_issues": [],
            },
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_graph_quality_user_prompt(
    *,
    graph: Dict[str, Any],
    evidence_graph_profile: Dict[str, Any],
    unit_scores: List[Dict[str, Any]],
    unit_statistics: Dict[str, Any],
    target_kg_goal: Optional[str],
) -> str:
    payload: Dict[str, Any] = {
        "task": "Evaluate overall scientific quality of an extracted node-edge graph.",
        "coverage_scope": "Coverage is limited to evidence-visible content because full paper text is not provided.",
        "target_kg_goal": target_kg_goal or "",
        "graph": graph,
        "evidence_graph_profile": evidence_graph_profile,
        "unit_scores": unit_scores,
        "unit_statistics": unit_statistics,
        "output_schema": {
            "dimension_scores": {
                "core_knowledge_coverage_score": "integer from 1 to 5",
                "scientific_value_density_score": "integer from 1 to 5",
                "claim_calibration_score": "integer from 1 to 5",
                "scope_and_context_preservation_score": "integer from 1 to 5",
                "structural_consistency_score": "integer from 1 to 5",
                "redundancy_noise_control_score": "integer from 1 to 5",
                "evidence_quality_distribution_score": "integer from 1 to 5",
            },
            "final_graph_quality_score": "integer from 1 to 5",
            "decision": "high_quality | usable_with_minor_revision | usable_with_major_revision | low_quality | discard",
            "unit_quality_distribution": {
                "high_quality_units": 0,
                "medium_quality_units": 0,
                "low_quality_units": 0,
                "discard_units": 0,
            },
            "coverage_confidence": "high | medium | low",
            "major_strengths": [],
            "major_quality_issues": [],
            "missing_core_knowledge": [],
            "overclaim_risks": [],
            "scope_missing_issues": [],
            "structural_issues": [],
            "recommended_revision_priorities": [],
            "rationale": "",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


class OpenAIQualityJudge:
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

    async def build_evidence_profile(
        self,
        *,
        graph: Dict[str, Any],
        target_kg_goal: Optional[str],
    ) -> Dict[str, Any]:
        return await self._complete_json(
            system_prompt=PROFILE_SYSTEM_PROMPT,
            user_prompt=build_profile_user_prompt(
                graph=graph,
                target_kg_goal=target_kg_goal,
            ),
        )

    async def evaluate_unit_batch(
        self,
        *,
        units: List[Dict[str, Any]],
        evidence_graph_profile: Dict[str, Any],
        target_kg_goal: Optional[str],
    ) -> Dict[str, Any]:
        return await self._complete_json(
            system_prompt=UNIT_QUALITY_SYSTEM_PROMPT,
            user_prompt=build_unit_quality_user_prompt(
                units=units,
                evidence_graph_profile=evidence_graph_profile,
                target_kg_goal=target_kg_goal,
            ),
        )

    async def evaluate_graph_quality(
        self,
        *,
        graph: Dict[str, Any],
        evidence_graph_profile: Dict[str, Any],
        unit_scores: List[Dict[str, Any]],
        unit_statistics: Dict[str, Any],
        target_kg_goal: Optional[str],
    ) -> Dict[str, Any]:
        return await self._complete_json(
            system_prompt=GRAPH_QUALITY_SYSTEM_PROMPT,
            user_prompt=build_graph_quality_user_prompt(
                graph=graph,
                evidence_graph_profile=evidence_graph_profile,
                unit_scores=unit_scores,
                unit_statistics=unit_statistics,
                target_kg_goal=target_kg_goal,
            ),
        )

    async def _complete_json(self, *, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
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
