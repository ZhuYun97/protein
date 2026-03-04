import os
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Type, TypeVar, Tuple, Optional
from pydantic import BaseModel, Field, ValidationError
from openai import OpenAI

from utils import extract_paper_sections

# 空内容时保存的 JSON 结构（与 pipeline 输出格式一致）
EMPTY_RESULT = {
    "01_initial_request": {},
    "02_agent_trajectory": [],
    "03_success_verification": {},
}
# API 失败时的重试次数（不含首次）
MAX_API_RETRIES = 3

T = TypeVar("T", bound=BaseModel)

# ==========================================
# 1. 定义独立的 Pydantic Schema
# ==========================================

# (保留之前的内部模型定义...)
class InitialRequest(BaseModel):
    target_name: str = Field(description="Name of the target product")
    input_data: str = Field(description="Input data or materials the experiment starts from")
    user_intent: str = Field(description="Core research motivation or objective")
    quantifiable_goal: str = Field(description="Final quantifiable property or metric to achieve")

class Tool(BaseModel):
    name: str = Field(description="Canonical tool name")
    version: str = Field(default="", description="Tool version (optional)")

class Reference(BaseModel):
    citation: str = Field(description="Citation or brief description of the reference")
    relevance: str = Field(description="How this reference supports or motivates this step")

class TrajectoryStep(BaseModel):
    step_index: int
    thought: str = Field(description="Format: [Background]...[Gap]...[Decision]...")
    action: str = Field(description="dry_experiment or wet_experiment")
    tool: Tool
    parameters: Dict[str, Any] = Field(description="Tool parameters extracted verbatim from the text, no fabrication")
    observation: str = Field(description="Direct result or data produced by this step")
    valid: bool = Field(description="Whether this step is valid. Mark False for ablations, controls, or steps with undesirable outcomes")
    references: List[Reference] = Field(default=[], description="References supporting the decision in this step")

class TrajectoryList(BaseModel):
    """由于大模型结构化输出通常需要一个对象作为根节点，这里做一层包装"""
    steps: List[TrajectoryStep]

class MetricDetail(BaseModel):
    value: str
    unit: str
    interpretation: str

class SuccessVerification(BaseModel):
    validation_technique: str
    metrics: Dict[str, MetricDetail]
    final_verdict: str

class TrajectoryAndVerification(BaseModel):
    steps: List[TrajectoryStep]
    verification: SuccessVerification

# ==========================================
# 2. 定义分层抽取 Pipeline
# ==========================================

class ProteinPaperExtractor:
    def __init__(self):
        self.client = OpenAI(
            base_url="http://35.220.164.252:3888/v1/",
            api_key="xxx"
        )
        self.model_name = 'qwen3.5-397b-a17b'
        self._supports_structured_output = self._probe_structured_output()

    def _probe_structured_output(self) -> bool:
        """探测 API 是否支持 json_schema 结构化输出"""
        try:
            self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": "返回 {\"ok\": true}"}],
                max_tokens=10,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "probe",
                        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                        "strict": True
                    }
                }
            )
            print("  [检测] API 支持 Structured Outputs，将直接传入 Schema。")
            return True
        except Exception:
            print("  [检测] API 不支持 Structured Outputs，将在 Prompt 中注入 Schema 并启用重试。")
            return False

    def _timed_request(self, **kwargs) -> object:
        """执行 API 请求并打印耗时"""
        t0 = time.time()
        response = self.client.chat.completions.create(**kwargs)
        elapsed = time.time() - t0
        print(f"  [耗时] {elapsed:.1f}s")
        return response

    def _call_with_prompt_injection(self, prompt: str, model_class: Type[T], max_retries: int = 3) -> T:
        """Prompt 注入 Schema + 重试"""
        schema_hint = (
            "\n\nReturn your response strictly following this JSON Schema. All fields are required:\n"
            + json.dumps(model_class.model_json_schema(), indent=2, ensure_ascii=False)
        )
        full_prompt = prompt + schema_hint
        for attempt in range(max_retries):
            response = self._timed_request(
                model=self.model_name,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            try:
                return model_class.model_validate_json(response.choices[0].message.content)
            except ValidationError as e:
                if attempt == max_retries - 1:
                    raise
                print(f"  [重试 {attempt + 1}/{max_retries}] 校验失败: {e}")

    def _call(self, prompt: str, model_class: Type[T], max_retries: int = 3) -> T:
        """统一调用入口：优先用 json_schema，校验失败时自动降级为 prompt 注入 + 重试"""
        if self._supports_structured_output:
            try:
                response = self._timed_request(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": model_class.__name__,
                            "schema": model_class.model_json_schema(),
                            "strict": True
                        }
                    }
                )
                return model_class.model_validate_json(response.choices[0].message.content)
            except (ValidationError, Exception) as e:
                print(f"  [降级] Structured Outputs 返回无效数据，切换为 Prompt 注入模式。原因: {e}")
                return self._call_with_prompt_injection(prompt, model_class, max_retries)
        else:
            return self._call_with_prompt_injection(prompt, model_class, max_retries)

    def extract_initial_request(self, abstract_intro_text: str) -> InitialRequest:
        """Stage 1: Extract initial request from abstract and introduction"""
        print("-> Extracting Stage 1: Initial Request (01_initial_request)...")
        prompt = f"""
        You are a computational biology expert. Read the Abstract and Introduction of the following paper and extract the initial research request.

        Extract the following fields:
        - target_name: Name of the target product (e.g., a protein, enzyme, or molecule)
        - input_data: Input data or starting materials the experiment depends on
        - user_intent: The researcher's core motivation or objective
        - quantifiable_goal: The final quantifiable property or metric aimed for

        Paper text:
        {abstract_intro_text}
        """
        
        return self._call(prompt, InitialRequest)

    def extract_trajectory(self, methods_text: str, initial_request: InitialRequest) -> TrajectoryList:
        """Stage 2: Extract agent trajectory from methods"""
        print("-> Extracting Stage 2: Agent Trajectory (02_agent_trajectory)...")
        prompt = f"""
        You are a computational biology AI Agent performing a retrospective analysis.
        Based on the research objective below, read the Methods and Supplementary Information (SI), and reverse-engineer the experimental steps into an execution trajectory.

        [Research Objective]:
        {initial_request.model_dump_json(indent=2)}

        [Methods Text]:
        {methods_text}

        ## 1. Internal Planning (do not output)
        Before generating JSON, perform the following reasoning internally:
        1. **Operation Enumeration**: List all explicit computational or wet-lab operations in the text.
        2. **Phase Mapping**: Map operations to the standard workflow (Generation -> Sequence Design -> Screening -> Validation).
        3. **Granularity Calibration (mandatory rules, no exceptions)**:
           - **One step must contain exactly one tool.** Different tools (e.g., RifGen, RifDock, RosettaDesign) described in the same paragraph must be split into separate steps. Merging is never allowed.
           - *Must split when*:
             * The tool name changes (highest priority, no exceptions)
             * Parameters or configuration change (e.g., two rounds of sequence design with different settings)
             * An intermediate result is produced and consumed by a subsequent step
             * A human screening, evaluation, or decision point exists
           - *Only allowed to merge when*: exact same tool + exact same parameters + no intermediate artifacts + only increasing sampling volume (e.g., running the same script N times).

        ## 2. Output JSON
        After internal planning, extract each step in the following format:
        - step_index: Step number starting from 1
        - thought: Fill strictly using the template: "[Background] What is currently known/completed: [content]. [Gap] What is currently missing/unresolved: [content]. [Decision] To achieve [goal], chose to use [tool name] to perform [specific operation] on [target], expecting to obtain [expected output]."
        - action: Classify as dry_experiment (computational/simulation) or wet_experiment (laboratory operation)
        - tool: Tool or method used, with name and version (leave version empty string if not mentioned)
        - parameters: Tool parameters extracted from the text. All values must be grounded in the source text — do not fabricate. Extract comprehensively, including:
            * Numeric thresholds: counts ("1,615 scaffolds"), cutoffs ("top 50,000"), scores ("pLDDT > 92"), RMSD, temperature, iterations, etc.
            * Input data: databases, sequence sets, starting structures (e.g., "2,000 naturally occurring NTF2s")
            * Filtering criteria: filter standards, scoring functions, loss function components
            * Algorithm configuration: sampling strategy (e.g., MCMC), allowed operations (e.g., insertions and deletions)
            * Substrates and targets: names and conformations of operands (e.g., "anionic DTZ conformers")
          If a parameter is only described qualitatively (e.g., "high-affinity") without a numeric value, still extract that description as the parameter value.
        - observation: The direct result or data feedback produced by this step
        - valid: Boolean. Mark False for:
            * Ablation study steps using alternative tools/methods with suboptimal results
            * Control experiments proving a method is not viable
            * Steps explicitly negated or overturned by subsequent steps
          Mark True for all other steps that execute normally and produce expected results.
        - references: List of references supporting this step's decision, each containing:
            - citation: The reference cited in the paper (e.g., author, year, title fragment, or number)
            - relevance: How this reference provides evidence or inspiration for this step
          Return an empty list if no explicit references support this step.
        """

        return self._call(prompt, TrajectoryList)

    def extract_trajectory_and_verification(self, paper_text: str, initial_request: InitialRequest) -> TrajectoryAndVerification:
        """Stage 2+3 (merged): Extract execution trajectory and success verification"""
        print("-> Extracting Stage 2+3: Trajectory & Verification...")
        prompt = f"""
        You are a computational biology AI Agent performing a retrospective analysis.
        Based on the research objective below, read the full paper text and complete the following two tasks.

        [Research Objective]:
        {initial_request.model_dump_json(indent=2)}

        [Paper Text]:
        {paper_text}

        ## 1. Internal Planning (do not output)
        Before generating JSON, perform the following reasoning internally:
        1. **Operation Enumeration**: List all explicit computational or wet-lab operations in the text.
        2. **Phase Mapping**: Map operations to the standard workflow (Generation -> Sequence Design -> Screening -> Validation).
        3. **Granularity Calibration (mandatory rules, no exceptions)**:
           - **One step must contain exactly one tool.** Different tools (e.g., RifGen, RifDock, RosettaDesign) described in the same paragraph must be split into separate steps. Merging is never allowed.
           - *Must split when*:
             * The tool name changes (highest priority, no exceptions)
             * Parameters or configuration change (e.g., two rounds of sequence design with different settings)
             * An intermediate result is produced and consumed by a subsequent step
             * A human screening, evaluation, or decision point exists
           - *Only allowed to merge when*: exact same tool + exact same parameters + no intermediate artifacts + only increasing sampling volume (e.g., running the same script N times).

        ## 2. Output JSON
        Output two sections:

        ### steps (execution trajectory)
        Extract each step in the following format:
        - step_index: Step number starting from 1
        - thought: Fill strictly using the template: "[Background] What is currently known/completed: [content]. [Gap] What is currently missing/unresolved: [content]. [Decision] To achieve [goal], chose to use [tool name] to perform [specific operation] on [target], expecting to obtain [expected output]."
        - action: Classify as dry_experiment (computational/simulation) or wet_experiment (laboratory operation)
        - tool: Tool or method used, with name and version (leave version empty string if not mentioned)
        - parameters: Tool parameters extracted from the text. All values must be grounded in the source text — do not fabricate. Extract comprehensively, including:
            * Numeric thresholds: counts ("1,615 scaffolds"), cutoffs ("top 50,000"), scores ("pLDDT > 92"), RMSD, temperature, iterations, etc.
            * Input data: databases, sequence sets, starting structures (e.g., "2,000 naturally occurring NTF2s")
            * Filtering criteria: filter standards, scoring functions, loss function components
            * Algorithm configuration: sampling strategy (e.g., MCMC), allowed operations (e.g., insertions and deletions)
            * Substrates and targets: names and conformations of operands (e.g., "anionic DTZ conformers")
          If a parameter is only described qualitatively (e.g., "high-affinity") without a numeric value, still extract that description as the parameter value.
        - observation: The direct result or data feedback produced by this step
        - valid: Boolean. Mark False for:
            * Ablation study steps using alternative tools/methods with suboptimal results
            * Control experiments proving a method is not viable
            * Steps explicitly negated or overturned by subsequent steps
          Mark True for all other steps that execute normally and produce expected results.
        - references: List of references supporting this step's decision, each containing:
            - citation: The reference cited in the paper (e.g., author, year, title fragment, or number)
            - relevance: How this reference provides evidence or inspiration for this step
          Return an empty list if no explicit references support this step.

        ### verification (success verification)
        Compare against the initial research objective and extract from Results:
        - validation_technique: Validation method used
        - metrics: Dictionary of quantitative metrics, each containing value, unit, and interpretation
        - final_verdict: Final conclusion on whether the initial objective was achieved
        """

        return self._call(prompt, TrajectoryAndVerification)

    def run_pipeline(self, abstract_intro: str, paper_text: str) -> Dict:
        """组装完整的抽取结果"""
        pipeline_start = time.time()

        # 1. 抽取初始请求
        t1 = time.time()
        step_01 = self.extract_initial_request(abstract_intro)
        print(f"  [Stage 1 耗时] {time.time() - t1:.1f}s")

        # 2+3. 合并抽取轨迹与验证结果
        t2 = time.time()
        step_02_03 = self.extract_trajectory_and_verification(paper_text, step_01)
        print(f"  [Stage 2+3 耗时] {time.time() - t2:.1f}s")

        total = time.time() - pipeline_start
        print(f"\n[总耗时] {total:.1f}s")

        # 4. 组装为最终嵌套 JSON 结构
        final_result = {
            "01_initial_request": step_01.model_dump(),
            "02_agent_trajectory": [step.model_dump() for step in step_02_03.steps],
            "03_success_verification": step_02_03.verification.model_dump()
        }

        return final_result


def process_one_file(
    paper_path: str,
    output_dir: str,
) -> Tuple[str, bool, Optional[str]]:
    """
    处理单篇论文：读取 → 若为空则写空 JSON，否则跑 pipeline 并保存。
    API 失败时会重试 MAX_API_RETRIES 次。返回 (output_path, success, error_message)。
    """
    input_basename = os.path.splitext(os.path.basename(paper_path))[0]
    output_path = os.path.join(output_dir, input_basename + ".json")
    try:
        with open(paper_path, "r", encoding="utf-8") as f:
            full_text = f.read()
        if not full_text.strip():
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(EMPTY_RESULT, f, indent=2, ensure_ascii=False)
            return (output_path, True, None)
        abstract_intro, _ = extract_paper_sections(full_text)
        last_error = None
        for attempt in range(MAX_API_RETRIES + 1):
            try:
                extractor = ProteinPaperExtractor()
                final_json = extractor.run_pipeline(abstract_intro, full_text)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(final_json, f, indent=2, ensure_ascii=False)
                return (output_path, True, None)
            except Exception as e:
                last_error = e
                if attempt < MAX_API_RETRIES:
                    time.sleep(1.0 * (attempt + 1))  # 递增等待再重试
        return (output_path, False, str(last_error))
    except Exception as e:
        return (output_path, False, str(e))


# ==========================================
# 3. 执行：单文件 / 批量高并发
# ==========================================
PREPROCESSED_DIR = "/mnt/dhwfile/raise/user/zhuyun/protein_data_pipeline/PoC_Paper_154/preprocessed"
OUTPUT_DIR = "PoC_Paper_154_CoTs"
DEFAULT_MAX_WORKERS = 32

if __name__ == "__main__":
    # 用法: python extract_EN.py [输入路径] [并发数]
    # 输入路径: 可为单个文件或目录；默认目录为 PREPROCESSED_DIR
    # 若为目录则处理其下所有文件（高并发）；若为文件则只处理该文件
    input_path = sys.argv[1] if len(sys.argv) > 1 else PREPROCESSED_DIR
    max_workers = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_WORKERS

    if os.path.isfile(input_path):
        # 单文件模式
        paper_path = input_path
        if not os.path.isfile(paper_path):
            print(f"错误: 文件不存在 {paper_path}")
            sys.exit(1)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        out_path, ok, err = process_one_file(paper_path, OUTPUT_DIR)
        if ok:
            print(f"结果已保存至 {out_path}")
        else:
            print(f"Pipeline 运行出错: {err}")
            sys.exit(1)
    elif os.path.isdir(input_path):
        # 批量高并发模式
        preprocessed_dir = input_path
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        all_files = [
            os.path.join(preprocessed_dir, f)
            for f in os.listdir(preprocessed_dir)
            if os.path.isfile(os.path.join(preprocessed_dir, f))
        ]
        total = len(all_files)
        print(f"共 {total} 个文件，并发数 {max_workers}，开始处理...")
        start = time.time()
        done = 0
        failed = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(process_one_file, path, OUTPUT_DIR): path
                for path in all_files
            }
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                done += 1
                try:
                    out_path, ok, err = future.result()
                    if ok:
                        print(f"  [{done}/{total}] OK: {os.path.basename(path)}")
                    else:
                        print(f"  [{done}/{total}] FAIL: {os.path.basename(path)} — {err}")
                        failed.append((path, err))
                except Exception as e:
                    print(f"  [{done}/{total}] FAIL: {os.path.basename(path)} — {e}")
                    failed.append((path, str(e)))
        elapsed = time.time() - start
        print(f"\n完成: {total - len(failed)}/{total} 成功，总耗时 {elapsed:.1f}s")
        if failed:
            print("失败列表:")
            for p, e in failed:
                print(f"  - {p}: {e}")
    else:
        print(f"错误: 路径不存在或既非文件也非目录: {input_path}")
        print("用法: python extract_EN.py [输入路径(文件或目录)] [并发数]")
        sys.exit(1)