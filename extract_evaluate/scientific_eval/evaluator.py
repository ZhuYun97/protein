from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json
from .judge import OpenAIJudge


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class ScientificExtractionEvaluator:
    """Evaluate graph extraction results at node and edge granularity."""

    def __init__(
        self,
        *,
        input_path: Path,
        model: str,
        openai_base_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        max_concurrency: int = 8,
        dry_run: bool = False,
    ) -> None:
        self.input_path = input_path
        self.model = model
        self.max_concurrency = max(1, max_concurrency)
        self.dry_run = dry_run
        self.judge = None
        if not dry_run:
            self.judge = OpenAIJudge(
                model=model,
                base_url=openai_base_url,
                api_key=openai_api_key,
            )

    def evaluate(self) -> Dict[str, Any]:
        graph = read_json(self.input_path)
        tasks, validation_errors = self._build_tasks(graph)

        if self.dry_run:
            item_results = [self._dry_run_result(task) for task in tasks]
        else:
            item_results = asyncio.run(self._evaluate_tasks(tasks))

        node_results = [item for item in item_results if item["unit_type"] == "node"]
        edge_results = [item for item in item_results if item["unit_type"] == "edge"]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_file": str(self.input_path),
            "model": self.model,
            "dry_run": self.dry_run,
            "graph_metadata": graph.get("graph_metadata", {}),
            "summary": {
                "node_count": len(node_results),
                "edge_count": len(edge_results),
                "item_count": len(item_results),
                "average_score": _mean(self._scores(item_results)),
                "node_average_score": _mean(self._scores(node_results)),
                "edge_average_score": _mean(self._scores(edge_results)),
                "validation_error_count": len(validation_errors),
            },
            "validation_errors": validation_errors,
            "nodes": node_results,
            "edges": edge_results,
        }

    def save(self, report: Dict[str, Any], output_path: Path) -> None:
        write_json(output_path, report)

    async def _evaluate_tasks(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        assert self.judge is not None
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_one(task: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                try:
                    result = await self.judge.evaluate_unit(task)
                except Exception as exc:
                    return self._error_result(task, exc)
                return self._merge_result(task, result)

        return await asyncio.gather(*(run_one(task) for task in tasks))

    def _build_tasks(self, graph: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[str]]:
        nodes = _as_list(graph.get("nodes"))
        edges = _as_list(graph.get("edges"))
        validation_errors: List[str] = []
        tasks: List[Dict[str, Any]] = []
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

            tasks.append(
                {
                    "unit_type": "node",
                    "unit_id": node_id,
                    "source_index": index,
                    "evidence": evidence,
                    "payload": {
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
            unit_id = f"edge_{index + 1}:{source_id}->{target_id}:{edge_type}"
            tasks.append(
                {
                    "unit_type": "edge",
                    "unit_id": unit_id,
                    "source_index": index,
                    "evidence": evidence,
                    "payload": {
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

        return tasks, validation_errors

    def _node_context(self, node: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not node:
            return {"name": "", "description": ""}
        return {
            "name": node.get("name", ""),
            "description": node.get("description", ""),
        }

    def _merge_result(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        merged = {
            "unit_id": task["unit_id"],
            "unit_type": task["unit_type"],
            "source_index": task["source_index"],
            "score": self._score_value(result.get("score")),
            "verdict": result.get("verdict", ""),
            "supported_points": _as_list(result.get("supported_points")),
            "problems": _as_list(result.get("problems")),
            "evidence_quality": result.get("evidence_quality", ""),
            "payload": task["payload"],
            "evidence": task["evidence"],
        }
        if task["unit_type"] == "edge":
            merged["source_node"] = task.get("source_node", {})
            merged["target_node"] = task.get("target_node", {})
        return merged

    def _dry_run_result(self, task: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "unit_id": task["unit_id"],
            "unit_type": task["unit_type"],
            "source_index": task["source_index"],
            "score": None,
            "verdict": "dry_run: request constructed, judge call skipped",
            "supported_points": [],
            "problems": [],
            "evidence_quality": "",
            "payload": task["payload"],
            "evidence": task["evidence"],
        }
        if task["unit_type"] == "edge":
            result["source_node"] = task.get("source_node", {})
            result["target_node"] = task.get("target_node", {})
        return result

    def _error_result(self, task: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        result = self._dry_run_result(task)
        result["verdict"] = "judge_error: request failed"
        result["problems"] = [f"{type(exc).__name__}: {exc}"]
        return result

    def _score_value(self, value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            score = float(value)
        elif isinstance(value, str):
            try:
                score = float(value.strip())
            except ValueError:
                return None
        else:
            return None
        return min(5.0, max(1.0, score))

    def _scores(self, items: List[Dict[str, Any]]) -> List[float]:
        scores: List[float] = []
        for item in items:
            score = item.get("score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        return scores
