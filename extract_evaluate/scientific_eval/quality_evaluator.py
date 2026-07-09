from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .io_utils import read_json, write_json
from .quality_judge import OpenAIQualityJudge


UNIT_ISSUE_TAGS = {
    "low_scientific_value",
    "weak_evidence",
    "overclaim",
    "missing_scope",
    "missing_uncertainty",
    "generic_relation",
    "wrong_relation_granularity",
    "ambiguous_node_type",
    "redundant_node",
    "redundant_edge",
    "relation_target_imprecise",
    "missing_intermediate_concept",
    "missing_core_finding",
    "missing_limitation",
    "poor_graph_structure",
    "failed_factuality_gate",
}


GRAPH_DIMENSION_KEYS = [
    "core_knowledge_coverage_score",
    "scientific_value_density_score",
    "claim_calibration_score",
    "scope_and_context_preservation_score",
    "structural_consistency_score",
    "redundancy_noise_control_score",
    "evidence_quality_distribution_score",
]


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _edge_signature(source: str, target: str, edge_type: str) -> str:
    return f"{source}->{target}:{edge_type or 'UNKNOWN'}"


class ScientificQualityEvaluator:
    """Evaluate scientific quality of an extracted node-edge graph.

    This evaluator intentionally does not depend on full paper text. It uses only
    graph units, their evidence, optional factuality scores, and the whole graph
    structure.
    """

    def __init__(
        self,
        *,
        input_path: Path,
        model: str,
        factuality_report_path: Optional[Path] = None,
        target_kg_goal: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        max_concurrency: int = 4,
        batch_size: int = 10,
        dry_run: bool = False,
    ) -> None:
        self.input_path = input_path
        self.model = model
        self.factuality_report_path = factuality_report_path
        self.target_kg_goal = target_kg_goal
        self.max_concurrency = max(1, max_concurrency)
        self.batch_size = max(1, batch_size)
        self.dry_run = dry_run
        self.judge = None
        if not dry_run:
            self.judge = OpenAIQualityJudge(
                model=model,
                base_url=openai_base_url,
                api_key=openai_api_key,
            )

    def evaluate(self) -> Dict[str, Any]:
        graph = read_json(self.input_path)
        factuality_lookup = self._load_factuality_lookup()
        units, validation_errors = self._build_units(graph, factuality_lookup)

        if self.dry_run:
            evidence_graph_profile = self._dry_run_profile(units)
            unit_scores = [self._dry_run_unit_result(unit) for unit in units]
            unit_statistics = self._compute_unit_statistics(unit_scores)
            graph_quality_report = self._dry_run_graph_report(unit_statistics)
        else:
            evidence_graph_profile, unit_scores, unit_statistics, graph_quality_report = asyncio.run(
                self._evaluate_with_model(graph, units)
            )

        final_score = self._adjust_final_graph_score(graph_quality_report, unit_statistics)

        node_scores = [item for item in unit_scores if item["unit_kind"] == "node"]
        edge_scores = [item for item in unit_scores if item["unit_kind"] == "edge"]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_file": str(self.input_path),
            "model": self.model,
            "dry_run": self.dry_run,
            "evaluation_type": "scientific_quality",
            "quality_scope_note": (
                "Scientific quality is evaluated only from extracted nodes, edges, "
                "their evidence, optional factuality scores, and graph structure. "
                "No full-paper coverage claim is made."
            ),
            "target_kg_goal": self.target_kg_goal or "",
            "factuality_report_file": str(self.factuality_report_path) if self.factuality_report_path else None,
            "graph_metadata": graph.get("graph_metadata", {}),
            "summary": {
                "node_count": len(node_scores),
                "edge_count": len(edge_scores),
                "unit_count": len(unit_scores),
                "validation_error_count": len(validation_errors),
                "weighted_average_unit_quality": unit_statistics.get("weighted_average_unit_quality"),
                "mean_unit_quality": unit_statistics.get("mean_unit_quality"),
                "formula_graph_quality_score": graph_quality_report.get("formula_graph_quality_score"),
                "final_adjusted_graph_quality_score": final_score,
                "final_adjusted_decision": graph_quality_report.get("final_adjusted_decision"),
            },
            "validation_errors": validation_errors,
            "evidence_graph_profile": evidence_graph_profile,
            "unit_statistics": unit_statistics,
            "unit_quality_scores": unit_scores,
            "nodes": node_scores,
            "edges": edge_scores,
            "graph_quality_report": graph_quality_report,
            "final_adjusted_graph_quality_score": final_score,
        }

    def save(self, report: Dict[str, Any], output_path: Path) -> None:
        write_json(output_path, report)

    async def _evaluate_with_model(
        self,
        graph: Dict[str, Any],
        units: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
        assert self.judge is not None
        evidence_graph_profile = self._normalize_profile(
            await self.judge.build_evidence_profile(
                graph=graph,
                target_kg_goal=self.target_kg_goal,
            )
        )
        unit_scores = await self._evaluate_units(units, evidence_graph_profile)
        unit_statistics = self._compute_unit_statistics(unit_scores)
        graph_quality_report = self._normalize_graph_quality_report(
            await self.judge.evaluate_graph_quality(
                graph=graph,
                evidence_graph_profile=evidence_graph_profile,
                unit_scores=unit_scores,
                unit_statistics=unit_statistics,
                target_kg_goal=self.target_kg_goal,
            ),
            unit_statistics=unit_statistics,
        )
        return evidence_graph_profile, unit_scores, unit_statistics, graph_quality_report

    async def _evaluate_units(
        self,
        units: List[Dict[str, Any]],
        evidence_graph_profile: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        assert self.judge is not None

        scored_by_id: Dict[str, Dict[str, Any]] = {}
        units_to_judge: List[Dict[str, Any]] = []

        for unit in units:
            factuality_score = unit.get("factuality_score")
            if isinstance(factuality_score, (int, float)) and factuality_score <= 2:
                scored_by_id[unit["unit_id"]] = self._failed_factuality_gate_result(unit)
            else:
                units_to_judge.append(unit)

        batches = [
            units_to_judge[index : index + self.batch_size]
            for index in range(0, len(units_to_judge), self.batch_size)
        ]
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            async with semaphore:
                request_units = [self._unit_for_quality_prompt(unit) for unit in batch]
                try:
                    response = await self.judge.evaluate_unit_batch(
                        units=request_units,
                        evidence_graph_profile=evidence_graph_profile,
                        target_kg_goal=self.target_kg_goal,
                    )
                except Exception as exc:
                    return [self._judge_error_unit_result(unit, exc) for unit in batch]

                raw_scores = _as_list(response.get("unit_scores"))
                raw_by_id = {
                    _safe_text(item.get("unit_id")): item
                    for item in raw_scores
                    if isinstance(item, dict)
                }
                results: List[Dict[str, Any]] = []
                for unit in batch:
                    raw = raw_by_id.get(unit["unit_id"])
                    if raw is None:
                        results.append(
                            self._judge_error_unit_result(
                                unit,
                                RuntimeError("Judge response did not include this unit_id"),
                            )
                        )
                    else:
                        results.append(self._merge_unit_quality_result(unit, raw))
                return results

        batch_results = await asyncio.gather(*(run_batch(batch) for batch in batches))
        for batch in batch_results:
            for item in batch:
                scored_by_id[item["unit_id"]] = item

        return [scored_by_id.get(unit["unit_id"], self._dry_run_unit_result(unit)) for unit in units]

    def _build_units(
        self,
        graph: Dict[str, Any],
        factuality_lookup: Dict[str, float],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        nodes = _as_list(graph.get("nodes"))
        edges = _as_list(graph.get("edges"))
        validation_errors: List[str] = []
        units: List[Dict[str, Any]] = []
        node_by_id: Dict[str, Dict[str, Any]] = {}

        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                validation_errors.append(f"nodes[{index}] is not an object")
                continue

            node_id = _safe_text(node.get("id")) or f"node_{index + 1}"
            if node_id in node_by_id:
                validation_errors.append(f"duplicate node id: {node_id}")
            node_by_id[node_id] = node

            evidence = _as_list(node.get("evidence"))
            if not evidence:
                validation_errors.append(f"node {node_id} has no evidence")

            aliases = [node_id]
            units.append(
                {
                    "unit_id": node_id,
                    "unit_kind": "node",
                    "source_index": index,
                    "aliases": aliases,
                    "factuality_score": self._lookup_factuality_score(factuality_lookup, aliases),
                    "evidence": evidence,
                    "content": {
                        "id": node_id,
                        "type": node.get("type"),
                        "name": node.get("name"),
                        "description": node.get("description"),
                        "properties": node.get("properties", {}),
                    },
                }
            )

        for index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                validation_errors.append(f"edges[{index}] is not an object")
                continue

            source_id = _safe_text(edge.get("source"))
            target_id = _safe_text(edge.get("target"))
            source_node = node_by_id.get(source_id)
            target_node = node_by_id.get(target_id)
            if source_id and source_node is None:
                validation_errors.append(f"edge {index} references missing source node: {source_id}")
            if target_id and target_node is None:
                validation_errors.append(f"edge {index} references missing target node: {target_id}")

            evidence = _as_list(edge.get("evidence"))
            if not evidence:
                validation_errors.append(f"edge {index} has no evidence")

            edge_type = _safe_text(edge.get("type")) or "UNKNOWN"
            generated_edge_id = f"edge_{index + 1}:{source_id}->{target_id}:{edge_type}"
            provided_edge_id = _safe_text(edge.get("id"))
            unit_id = provided_edge_id or generated_edge_id
            aliases = [
                unit_id,
                generated_edge_id,
                _edge_signature(source_id, target_id, edge_type),
                f"edge_index:{index}",
            ]
            if provided_edge_id:
                aliases.append(provided_edge_id)

            units.append(
                {
                    "unit_id": unit_id,
                    "unit_kind": "edge",
                    "source_index": index,
                    "aliases": aliases,
                    "factuality_score": self._lookup_factuality_score(factuality_lookup, aliases),
                    "evidence": evidence,
                    "content": {
                        "id": provided_edge_id or None,
                        "source": source_id,
                        "target": target_id,
                        "type": edge.get("type"),
                        "description": edge.get("description"),
                        "properties": edge.get("properties", {}),
                    },
                    "source_node": self._node_context(source_node),
                    "target_node": self._node_context(target_node),
                }
            )

        return units, validation_errors

    def _node_context(self, node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not node:
            return {"id": "", "type": "", "name": "", "description": ""}
        return {
            "id": node.get("id", ""),
            "type": node.get("type", ""),
            "name": node.get("name", ""),
            "description": node.get("description", ""),
        }

    def _unit_for_quality_prompt(self, unit: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "unit_id": unit["unit_id"],
            "unit_kind": unit["unit_kind"],
            "factuality_score": unit.get("factuality_score"),
            "evidence": unit.get("evidence", []),
            "content": unit.get("content", {}),
        }
        if unit["unit_kind"] == "edge":
            payload["source_node"] = unit.get("source_node", {})
            payload["target_node"] = unit.get("target_node", {})
        return payload

    def _merge_unit_quality_result(self, unit: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        merged = {
            "unit_id": unit["unit_id"],
            "unit_kind": unit["unit_kind"],
            "source_index": unit["source_index"],
            "factuality_score": unit.get("factuality_score"),
            "scientific_value_score": self._score_value(result.get("scientific_value_score")),
            "evidence_strength_score": self._score_value(result.get("evidence_strength_score")),
            "claim_evidence_alignment_score": self._score_value(result.get("claim_evidence_alignment_score")),
            "scope_completeness_score": self._score_value(result.get("scope_completeness_score")),
            "kg_reusability_score": self._score_value(result.get("kg_reusability_score")),
            "final_unit_quality_score": self._score_value(result.get("final_unit_quality_score")),
            "decision": _safe_text(result.get("decision")),
            "issue_tags": self._issue_tags(result.get("issue_tags")),
            "quality_issues": _as_list(result.get("quality_issues")),
            "recommended_revision": _safe_text(result.get("recommended_revision")),
            "rationale": _safe_text(result.get("rationale")),
            "content": unit.get("content", {}),
            "evidence": unit.get("evidence", []),
        }
        if unit["unit_kind"] == "edge":
            merged["source_node"] = unit.get("source_node", {})
            merged["target_node"] = unit.get("target_node", {})
        self._apply_unit_caps(merged)
        return merged

    def _apply_unit_caps(self, item: Dict[str, Any]) -> None:
        final_score = item.get("final_unit_quality_score")
        if not isinstance(final_score, (int, float)):
            return

        cap_notes: List[str] = []
        factuality_score = item.get("factuality_score")
        if isinstance(factuality_score, (int, float)):
            if factuality_score <= 2:
                final_score = 1
                cap_notes.append("failed_factuality_gate")
                if "failed_factuality_gate" not in item["issue_tags"]:
                    item["issue_tags"].append("failed_factuality_gate")
            elif factuality_score == 3 and final_score > 3:
                final_score = 3
                cap_notes.append("factuality_score_3_caps_quality_at_3")

        alignment_score = item.get("claim_evidence_alignment_score")
        if isinstance(alignment_score, (int, float)) and alignment_score <= 2 and final_score > 2:
            final_score = 2
            cap_notes.append("claim_evidence_alignment_score_caps_quality_at_2")
            if "overclaim" not in item["issue_tags"]:
                item["issue_tags"].append("overclaim")

        value_score = item.get("scientific_value_score")
        if isinstance(value_score, (int, float)) and value_score <= 2 and final_score > 2:
            final_score = 2
            cap_notes.append("scientific_value_score_caps_quality_at_2")
            if "low_scientific_value" not in item["issue_tags"]:
                item["issue_tags"].append("low_scientific_value")

        item["final_unit_quality_score"] = int(final_score)
        item["decision"] = self._unit_decision(int(final_score))
        if cap_notes:
            item.setdefault("deterministic_caps_applied", []).extend(cap_notes)

    def _failed_factuality_gate_result(self, unit: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "unit_id": unit["unit_id"],
            "unit_kind": unit["unit_kind"],
            "source_index": unit["source_index"],
            "factuality_score": unit.get("factuality_score"),
            "scientific_value_score": 1,
            "evidence_strength_score": 1,
            "claim_evidence_alignment_score": 1,
            "scope_completeness_score": 1,
            "kg_reusability_score": 1,
            "final_unit_quality_score": 1,
            "decision": "discard",
            "issue_tags": ["failed_factuality_gate"],
            "quality_issues": ["Unit failed factuality gate, so scientific quality is set to discard."],
            "recommended_revision": "Remove or re-extract this unit after factuality correction.",
            "rationale": "factuality_score <= 2",
            "content": unit.get("content", {}),
            "evidence": unit.get("evidence", []),
            "deterministic_caps_applied": ["failed_factuality_gate"],
        }
        if unit["unit_kind"] == "edge":
            result["source_node"] = unit.get("source_node", {})
            result["target_node"] = unit.get("target_node", {})
        return result

    def _dry_run_unit_result(self, unit: Dict[str, Any]) -> Dict[str, Any]:
        factuality_score = unit.get("factuality_score")
        if isinstance(factuality_score, (int, float)) and factuality_score <= 2:
            return self._failed_factuality_gate_result(unit)

        result = {
            "unit_id": unit["unit_id"],
            "unit_kind": unit["unit_kind"],
            "source_index": unit["source_index"],
            "factuality_score": factuality_score,
            "scientific_value_score": None,
            "evidence_strength_score": None,
            "claim_evidence_alignment_score": None,
            "scope_completeness_score": None,
            "kg_reusability_score": None,
            "final_unit_quality_score": None,
            "decision": "dry_run",
            "issue_tags": [],
            "quality_issues": [],
            "recommended_revision": "",
            "rationale": "dry_run: request constructed, judge call skipped",
            "content": unit.get("content", {}),
            "evidence": unit.get("evidence", []),
        }
        if unit["unit_kind"] == "edge":
            result["source_node"] = unit.get("source_node", {})
            result["target_node"] = unit.get("target_node", {})
        return result

    def _judge_error_unit_result(self, unit: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        result = self._dry_run_unit_result(unit)
        result["decision"] = "judge_error"
        result["quality_issues"] = [f"{type(exc).__name__}: {exc}"]
        result["rationale"] = "Judge request failed or returned an unusable response."
        return result

    def _compute_unit_statistics(self, unit_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
        node_scores = self._final_scores([item for item in unit_scores if item["unit_kind"] == "node"])
        edge_scores = self._final_scores([item for item in unit_scores if item["unit_kind"] == "edge"])
        all_scores = self._final_scores(unit_scores)

        weighted_sum = 0.0
        weight_sum = 0.0
        for item in unit_scores:
            score = item.get("final_unit_quality_score")
            if not isinstance(score, (int, float)):
                continue
            weight = 1.2 if item["unit_kind"] == "edge" else 1.0
            weighted_sum += float(score) * weight
            weight_sum += weight

        high = sum(1 for score in all_scores if score >= 4)
        medium = sum(1 for score in all_scores if score == 3)
        low = sum(1 for score in all_scores if score == 2)
        discard = sum(1 for score in all_scores if score <= 1)
        scored_count = len(all_scores)
        low_or_discard = low + discard

        issue_counter: Counter[str] = Counter()
        for item in unit_scores:
            issue_counter.update(self._issue_tags(item.get("issue_tags")))

        return {
            "unit_count": len(unit_scores),
            "scored_unit_count": scored_count,
            "unscored_unit_count": len(unit_scores) - scored_count,
            "node_count": len([item for item in unit_scores if item["unit_kind"] == "node"]),
            "edge_count": len([item for item in unit_scores if item["unit_kind"] == "edge"]),
            "mean_unit_quality": _mean(all_scores),
            "node_average_quality": _mean(node_scores),
            "edge_average_quality": _mean(edge_scores),
            "weighted_average_unit_quality": round(weighted_sum / weight_sum, 4) if weight_sum else None,
            "high_quality_count": high,
            "medium_quality_count": medium,
            "low_quality_count": low,
            "discard_count": discard,
            "low_or_discard_count": low_or_discard,
            "high_quality_ratio": round(high / scored_count, 4) if scored_count else None,
            "medium_quality_ratio": round(medium / scored_count, 4) if scored_count else None,
            "low_quality_ratio": round(low_or_discard / scored_count, 4) if scored_count else None,
            "discard_ratio": round(discard / scored_count, 4) if scored_count else None,
            "factuality_gated_count": issue_counter.get("failed_factuality_gate", 0),
            "issue_tag_counts": dict(issue_counter.most_common()),
            "common_issues": [tag for tag, _ in issue_counter.most_common(10)],
        }

    def _normalize_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(profile, dict):
            profile = {}
        profile.setdefault("profile_source", "graph_and_unit_evidence_only")
        profile["coverage_confidence"] = _safe_text(profile.get("coverage_confidence")) or "low"
        profile["paper_or_study_type_guess"] = _safe_text(profile.get("paper_or_study_type_guess"))
        profile["evidence_visible_topic"] = _safe_text(profile.get("evidence_visible_topic"))
        profile["evidence_quality_score"] = self._score_value(profile.get("evidence_quality_score"))
        profile["evidence_visible_core_claims"] = _as_list(profile.get("evidence_visible_core_claims"))
        profile["major_limitations_from_available_evidence"] = _as_list(
            profile.get("major_limitations_from_available_evidence")
        )
        expected = profile.get("expected_kg_content")
        if not isinstance(expected, dict):
            expected = {}
        expected.setdefault("important_node_types", [])
        expected.setdefault("important_edge_types", [])
        expected.setdefault("must_capture_findings", [])
        expected.setdefault("must_capture_scope", [])
        expected.setdefault("optional_content", [])
        profile["expected_kg_content"] = expected
        profile["rationale"] = _safe_text(profile.get("rationale"))
        return profile

    def _normalize_graph_quality_report(
        self,
        report: Dict[str, Any],
        *,
        unit_statistics: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(report, dict):
            report = {}
        dimensions = report.get("dimension_scores")
        if not isinstance(dimensions, dict):
            dimensions = {}
        report["dimension_scores"] = {
            key: self._score_value(dimensions.get(key))
            for key in GRAPH_DIMENSION_KEYS
        }
        report["final_graph_quality_score"] = self._score_value(report.get("final_graph_quality_score"))
        report["decision"] = _safe_text(report.get("decision")) or self._graph_decision(
            report.get("final_graph_quality_score")
        )
        report["unit_quality_distribution"] = {
            "high_quality_units": unit_statistics.get("high_quality_count", 0),
            "medium_quality_units": unit_statistics.get("medium_quality_count", 0),
            "low_quality_units": unit_statistics.get("low_quality_count", 0),
            "discard_units": unit_statistics.get("discard_count", 0),
        }
        for key in [
            "major_strengths",
            "major_quality_issues",
            "missing_core_knowledge",
            "overclaim_risks",
            "scope_missing_issues",
            "structural_issues",
            "recommended_revision_priorities",
        ]:
            report[key] = _as_list(report.get(key))
        report["coverage_confidence"] = _safe_text(report.get("coverage_confidence")) or "low"
        report["rationale"] = _safe_text(report.get("rationale"))
        report["coverage_scope_note"] = (
            "Coverage score is evidence-scoped because full paper text is not provided."
        )
        return report

    def _adjust_final_graph_score(
        self,
        graph_quality_report: Dict[str, Any],
        unit_statistics: Dict[str, Any],
    ) -> Optional[int]:
        dimensions = graph_quality_report.get("dimension_scores", {})
        formula_score = self._formula_graph_quality_score(unit_statistics, dimensions)
        graph_quality_report["formula_graph_quality_score"] = formula_score

        candidates: List[int] = []
        llm_final = graph_quality_report.get("final_graph_quality_score")
        if isinstance(llm_final, (int, float)):
            candidates.append(int(llm_final))
        if isinstance(formula_score, (int, float)):
            candidates.append(self._round_score(formula_score))

        if not candidates:
            graph_quality_report["deterministic_adjustments"] = []
            graph_quality_report["final_adjusted_graph_quality_score"] = None
            graph_quality_report["final_adjusted_decision"] = "not_scored"
            return None

        final_score = min(candidates)
        adjustments: List[str] = []

        def apply_cap(cap: int, reason: str) -> None:
            nonlocal final_score
            if final_score > cap:
                final_score = cap
                adjustments.append(reason)

        coverage_score = dimensions.get("core_knowledge_coverage_score")
        value_density_score = dimensions.get("scientific_value_density_score")
        calibration_score = dimensions.get("claim_calibration_score")
        structural_score = dimensions.get("structural_consistency_score")
        low_quality_ratio = unit_statistics.get("low_quality_ratio")

        if isinstance(coverage_score, (int, float)) and coverage_score <= 2:
            apply_cap(3, "core_knowledge_coverage_score <= 2 caps final score at 3")
        if isinstance(value_density_score, (int, float)) and value_density_score <= 2:
            apply_cap(2, "scientific_value_density_score <= 2 caps final score at 2")
        if isinstance(calibration_score, (int, float)) and calibration_score <= 2:
            apply_cap(2, "claim_calibration_score <= 2 caps final score at 2")
        if isinstance(structural_score, (int, float)) and structural_score <= 2:
            apply_cap(3, "structural_consistency_score <= 2 caps final score at 3")
        if isinstance(low_quality_ratio, (int, float)):
            if low_quality_ratio > 0.50:
                apply_cap(2, "more than 50% of scored units have quality <= 2")
            elif low_quality_ratio > 0.30:
                apply_cap(3, "more than 30% of scored units have quality <= 2")

        graph_quality_report["deterministic_adjustments"] = adjustments
        graph_quality_report["final_adjusted_graph_quality_score"] = final_score
        graph_quality_report["final_adjusted_decision"] = self._graph_decision(final_score)
        return final_score

    def _formula_graph_quality_score(
        self,
        unit_statistics: Dict[str, Any],
        dimensions: Dict[str, Any],
    ) -> Optional[float]:
        average_unit_quality = unit_statistics.get("weighted_average_unit_quality")
        coverage = dimensions.get("core_knowledge_coverage_score")
        calibration = dimensions.get("claim_calibration_score")
        scope = dimensions.get("scope_and_context_preservation_score")
        structural = dimensions.get("structural_consistency_score")
        redundancy = dimensions.get("redundancy_noise_control_score")
        values = [average_unit_quality, coverage, calibration, scope, structural, redundancy]
        if not all(isinstance(value, (int, float)) for value in values):
            return None
        return round(
            0.40 * float(average_unit_quality)
            + 0.20 * float(coverage)
            + 0.15 * float(calibration)
            + 0.10 * float(scope)
            + 0.10 * float(structural)
            + 0.05 * float(redundancy),
            2,
        )

    def _load_factuality_lookup(self) -> Dict[str, float]:
        if not self.factuality_report_path:
            return {}
        report = read_json(self.factuality_report_path)
        lookup: Dict[str, float] = {}

        def add(key: Any, value: Any) -> None:
            text_key = _safe_text(key)
            score = self._score_value(value)
            if text_key and isinstance(score, (int, float)):
                lookup[text_key] = float(score)

        def add_report_item(item: Any) -> None:
            if not isinstance(item, dict):
                return
            score = self._extract_factuality_score(item)
            if score is None:
                return
            add(item.get("unit_id"), score)
            source_index = item.get("source_index")
            unit_type = _safe_text(item.get("unit_type")) or _safe_text(item.get("unit_kind"))
            if isinstance(source_index, int) and unit_type == "edge":
                add(f"edge_index:{source_index}", score)
            payload = item.get("payload") or item.get("content")
            if isinstance(payload, dict):
                add(payload.get("id"), score)
                source = _safe_text(payload.get("source"))
                target = _safe_text(payload.get("target"))
                edge_type = _safe_text(payload.get("type")) or "UNKNOWN"
                if source or target:
                    add(_edge_signature(source, target, edge_type), score)

        for item in _as_list(report.get("nodes")):
            add_report_item(item)
        for item in _as_list(report.get("edges")):
            add_report_item(item)
        for item in _as_list(report.get("unit_quality_scores")):
            add_report_item(item)

        for key, value in report.items():
            if key in {
                "generated_at",
                "input_file",
                "model",
                "dry_run",
                "graph_metadata",
                "summary",
                "validation_errors",
                "nodes",
                "edges",
                "unit_quality_scores",
                "graph_quality_report",
            }:
                continue
            score = self._extract_factuality_score(value)
            if score is not None:
                add(key, score)

        return lookup

    def _lookup_factuality_score(
        self,
        factuality_lookup: Dict[str, float],
        aliases: List[str],
    ) -> Optional[float]:
        for alias in aliases:
            if alias in factuality_lookup:
                return factuality_lookup[alias]
        return None

    def _extract_factuality_score(self, value: Any) -> Optional[float]:
        if isinstance(value, (int, float, str)):
            return self._score_value(value)
        if not isinstance(value, dict):
            return None
        for key in ["factuality_score", "score", "final_score", "final_unit_factuality_score"]:
            score = self._score_value(value.get(key))
            if isinstance(score, (int, float)):
                return float(score)
        return None

    def _score_value(self, value: Any) -> Optional[int]:
        if isinstance(value, (int, float)):
            raw = float(value)
        elif isinstance(value, str):
            try:
                raw = float(value.strip())
            except ValueError:
                return None
        else:
            return None
        return self._round_score(raw)

    def _round_score(self, value: float) -> int:
        return min(5, max(1, int(float(value) + 0.5)))

    def _final_scores(self, items: List[Dict[str, Any]]) -> List[float]:
        scores: List[float] = []
        for item in items:
            score = item.get("final_unit_quality_score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        return scores

    def _issue_tags(self, value: Any) -> List[str]:
        tags: List[str] = []
        for item in _as_list(value):
            tag = _safe_text(item)
            if tag in UNIT_ISSUE_TAGS and tag not in tags:
                tags.append(tag)
        return tags

    def _unit_decision(self, score: int) -> str:
        if score >= 4:
            return "keep"
        if score == 3:
            return "keep_with_revision"
        if score == 2:
            return "downweight"
        return "discard"

    def _graph_decision(self, score: Any) -> str:
        if not isinstance(score, (int, float)):
            return "not_scored"
        score_int = int(score)
        if score_int >= 5:
            return "high_quality"
        if score_int == 4:
            return "usable_with_minor_revision"
        if score_int == 3:
            return "usable_with_major_revision"
        if score_int == 2:
            return "low_quality"
        return "discard"

    def _dry_run_profile(self, units: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "profile_source": "graph_and_unit_evidence_only",
            "coverage_confidence": "low",
            "paper_or_study_type_guess": "",
            "evidence_visible_topic": "",
            "evidence_quality_score": None,
            "evidence_visible_core_claims": [],
            "major_limitations_from_available_evidence": [
                "dry_run: profile request constructed, judge call skipped"
            ],
            "expected_kg_content": {
                "important_node_types": sorted(
                    {
                        _safe_text(unit.get("content", {}).get("type"))
                        for unit in units
                        if unit["unit_kind"] == "node" and _safe_text(unit.get("content", {}).get("type"))
                    }
                ),
                "important_edge_types": sorted(
                    {
                        _safe_text(unit.get("content", {}).get("type"))
                        for unit in units
                        if unit["unit_kind"] == "edge" and _safe_text(unit.get("content", {}).get("type"))
                    }
                ),
                "must_capture_findings": [],
                "must_capture_scope": [],
                "optional_content": [],
            },
            "rationale": "dry_run: request constructed, judge call skipped",
        }

    def _dry_run_graph_report(self, unit_statistics: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "dimension_scores": {key: None for key in GRAPH_DIMENSION_KEYS},
            "final_graph_quality_score": None,
            "decision": "dry_run",
            "unit_quality_distribution": {
                "high_quality_units": unit_statistics.get("high_quality_count", 0),
                "medium_quality_units": unit_statistics.get("medium_quality_count", 0),
                "low_quality_units": unit_statistics.get("low_quality_count", 0),
                "discard_units": unit_statistics.get("discard_count", 0),
            },
            "coverage_confidence": "low",
            "major_strengths": [],
            "major_quality_issues": [],
            "missing_core_knowledge": [],
            "overclaim_risks": [],
            "scope_missing_issues": [],
            "structural_issues": [],
            "recommended_revision_priorities": [],
            "rationale": "dry_run: graph quality request constructed, judge call skipped",
            "coverage_scope_note": (
                "Coverage score is evidence-scoped because full paper text is not provided."
            ),
        }
