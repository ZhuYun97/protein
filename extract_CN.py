import os
import json
import time
from typing import List, Dict, Any, Type, TypeVar
from pydantic import BaseModel, Field, ValidationError
from openai import OpenAI

T = TypeVar("T", bound=BaseModel)

# ==========================================
# 1. 定义独立的 Pydantic Schema
# ==========================================

# (保留之前的内部模型定义...)
class InitialRequest(BaseModel):
    target_name: str = Field(description="目标产物名称")
    input_data: str = Field(description="依赖的输入数据，即实验基于什么材料或数据集开始")
    user_intent: str = Field(description="用户/研究者的核心需求或动机")
    quantifiable_goal: str = Field(description="最终希望达到的可量化性质或指标")

class Tool(BaseModel):
    name: str = Field(description="工具的规范名称")
    version: str = Field(default="", description="工具版本号（可选）")

class Reference(BaseModel):
    citation: str = Field(description="参考文献的原文引用或简短描述")
    relevance: str = Field(description="该文献如何支持或启发本步骤的决策")

class TrajectoryStep(BaseModel):
    step_index: int
    thought: str = Field(description="格式：【背景】...【问题/缺口】...【决策】...")
    action: str = Field(description="dry_experiment 或 wet_experiment")
    tool: Tool
    parameters: Dict[str, Any] = Field(description="工具参数及取值，必须来自原文，不得推断或编造")
    observation: str = Field(description="该步骤产生的直接结果或数据反馈")
    valid: bool = Field(description="该步骤是否有效。消融实验、对照组、产生不理想结果的步骤标记为 False")
    references: List[Reference] = Field(default=[], description="支持本步骤决策的参考文献或实验依据")

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
            "\n\n请严格按照以下 JSON Schema 返回，不得缺少任何字段：\n"
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
        """阶段一：从摘要和引言中抽取初始请求"""
        print("-> 正在抽取阶段一：初始请求 (01_initial_request)...")
        prompt = f"""
        你是一个计算生物学专家。请阅读以下论文的 Abstract 和 Introduction 部分，提取研究的初始请求。

        请提取以下字段：
        - target_name：目标产物名称（如某种蛋白质、酶或分子）
        - input_data：依赖的输入数据，即实验基于什么材料或数据集开始
        - user_intent：用户/研究者的核心需求或动机
        - quantifiable_goal：最终希望达到的可量化性质或指标

        文本内容:
        {abstract_intro_text}
        """
        
        return self._call(prompt, InitialRequest)

    def extract_trajectory(self, methods_text: str, initial_request: InitialRequest) -> TrajectoryList:
        """阶段二：带着阶段一的目标，从方法论中抽取 Agent 轨迹"""
        print("-> 正在抽取阶段二：执行轨迹 (02_agent_trajectory)...")
        prompt = f"""
        你是一个正在复盘的计算生物学 AI Agent。
        基于研究核心目标（如下），请阅读 Methods 和补充材料（SI），将实验步骤逆向工程为执行轨迹。

        【研究目标】:
        {initial_request.model_dump_json(indent=2)}

        【方法论文本】:
        {methods_text}

        ## 1. Internal Planning（隐式执行）
        在生成 JSON 之前，你必须在后台进行以下逻辑推演（不要输出）：
        1. **操作枚举**：列出文中所有明确的计算或湿实验操作。
        2. **阶段映射**：将操作映射到标准流程（生成 -> 序列设计 -> 筛选 -> 验证）。
        3. **颗粒度校准（强制规则，不得违反）**：
           - **一个 step 只能包含一个工具**。RifGen、RifDock、RosettaDesign 等不同工具即使在同一段落中描述，也必须拆分为独立 step，绝不允许合并。
           - *必须拆分的情况*：
             * 工具名称发生变化（最高优先级，无例外）
             * 参数或配置发生变化（如 sequence design 的两轮参数不同）
             * 步骤之间产生中间结果并被后续步骤消费
             * 存在人工筛选、评估或决策节点
           - *唯一允许合并的情况*：完全相同的工具 + 完全相同的参数 + 无中间产物 + 仅为增加采样量（如重复运行同一脚本 N 次）。

        ## 2. 输出 JSON
        完成内部规划后，将每一步提取为以下格式：
        - step_index: 步骤编号（从1开始）
        - thought: 严格按照模板填写："【背景】当前已知/已完成：[内容]。【问题/缺口】目前缺少/尚未解决：[内容]。【决策】为了[目标]，选择使用[工具名]对[操作对象]执行[具体操作]，预期获得[期望输出]。"
        - action: 判断该步骤是 dry_experiment（计算/模拟）还是 wet_experiment（实验室操作）
        - tool: 该步骤使用的工具或方法，包含 name 和 version（无版本号则留空字符串）
        - parameters: 该工具的参数及其取值，所有参数值必须有原文依据，不得凭空编造。请尽可能全面提取，包括但不限于：
            * 数值与阈值：如数量（"1,615 scaffolds"）、截止值（"top 50,000"）、评分（"pLDDT > 92"）、RMSD、温度、迭代次数等
            * 输入数据：使用的数据库、序列集合、起始结构（如 "2,000 naturally occurring NTF2s"）
            * 筛选条件：filter 标准、评分函数、loss function 组成
            * 算法配置：采样方式（如 MCMC）、允许的操作类型（如 insertions and deletions）
            * 底物与靶标：操作对象的名称、构型（如 "anionic DTZ conformers"）
          若原文对某参数仅定性描述（如"high-affinity"）而无具体数值，仍需提取该描述作为参数值。
        - observation: 该步骤实际产生的直接结果或数据反馈
        - valid: 布尔值，判断该步骤是否为有效步骤。以下情况标记为 False：
            * 消融实验（ablation study）中使用替代工具/方法但结果不理想
            * 对照组实验，结果证明该方法不可行
            * 明确被后续步骤否定或推翻的操作
          其余正常执行且结果符合预期的步骤标记为 True。
        - references: 支持本步骤决策的参考文献列表，每条包含：
            - citation: 论文中提及的文献引用（如作者、年份、标题片段或编号）
            - relevance: 该文献如何为本步骤提供依据或启发
          若该步骤无明确文献支持，返回空列表。
        """
        
        return self._call(prompt, TrajectoryList)

    def extract_trajectory_and_verification(self, paper_text: str, initial_request: InitialRequest) -> TrajectoryAndVerification:
        """阶段二+三（合并）：同时抽取执行轨迹与验收结论"""
        print("-> 正在抽取阶段二+三：执行轨迹 & 验证结果...")
        prompt = f"""
        你是一个正在复盘的计算生物学 AI Agent。
        基于研究核心目标（如下），请阅读论文正文，完成以下两项任务。

        【研究目标】:
        {initial_request.model_dump_json(indent=2)}

        【论文正文】:
        {paper_text}

        ## 1. Internal Planning（隐式执行）
        在生成 JSON 之前，你必须在后台进行以下逻辑推演（不要输出）：
        1. **操作枚举**：列出文中所有明确的计算或湿实验操作。
        2. **阶段映射**：将操作映射到标准流程（生成 -> 序列设计 -> 筛选 -> 验证）。
        3. **颗粒度校准（强制规则，不得违反）**：
           - **一个 step 只能包含一个工具**。RifGen、RifDock、RosettaDesign 等不同工具即使在同一段落中描述，也必须拆分为独立 step，绝不允许合并。
           - *必须拆分的情况*：
             * 工具名称发生变化（最高优先级，无例外）
             * 参数或配置发生变化（如 sequence design 的两轮参数不同）
             * 步骤之间产生中间结果并被后续步骤消费
             * 存在人工筛选、评估或决策节点
           - *唯一允许合并的情况*：完全相同的工具 + 完全相同的参数 + 无中间产物 + 仅为增加采样量（如重复运行同一脚本 N 次）。

        ## 2. 输出 JSON
        输出包含两个部分：

        ### steps（执行轨迹）
        将每一步提取为以下格式：
        - step_index: 步骤编号（从1开始）
        - thought: 严格按照模板填写："【背景】当前已知/已完成：[内容]。【问题/缺口】目前缺少/尚未解决：[内容]。【决策】为了[目标]，选择使用[工具名]对[操作对象]执行[具体操作]，预期获得[期望输出]。"
        - action: 判断该步骤是 dry_experiment（计算/模拟）还是 wet_experiment（实验室操作）
        - tool: 该步骤使用的工具或方法，包含 name 和 version（无版本号则留空字符串）
        - parameters: 该工具的参数及其取值，所有参数值必须有原文依据，不得凭空编造。请尽可能全面提取，包括但不限于：
            * 数值与阈值：如数量（"1,615 scaffolds"）、截止值（"top 50,000"）、评分（"pLDDT > 92"）、RMSD、温度、迭代次数等
            * 输入数据：使用的数据库、序列集合、起始结构（如 "2,000 naturally occurring NTF2s"）
            * 筛选条件：filter 标准、评分函数、loss function 组成
            * 算法配置：采样方式（如 MCMC）、允许的操作类型（如 insertions and deletions）
            * 底物与靶标：操作对象的名称、构型（如 "anionic DTZ conformers"）
          若原文对某参数仅定性描述（如"high-affinity"）而无具体数值，仍需提取该描述作为参数值。
        - observation: 该步骤实际产生的直接结果或数据反馈
        - valid: 布尔值，判断该步骤是否为有效步骤。以下情况标记为 False：
            * 消融实验（ablation study）中使用替代工具/方法但结果不理想
            * 对照组实验，结果证明该方法不可行
            * 明确被后续步骤否定或推翻的操作
          其余正常执行且结果符合预期的步骤标记为 True。
        - references: 支持本步骤决策的参考文献列表，每条包含：
            - citation: 论文中提及的文献引用（如作者、年份、标题片段或编号）
            - relevance: 该文献如何为本步骤提供依据或启发
          若该步骤无明确文献支持，返回空列表。

        ### verification（验收结论）
        对比研究初始目标，从 Results 中提取：
        - validation_technique: 使用的验证方法
        - metrics: 量化指标字典，每个指标包含 value、unit、interpretation
        - final_verdict: 是否达成初始目标的最终结论
        """

        return self._call(prompt, TrajectoryAndVerification)

    def run_pipeline(self, abstract_intro: str, paper_text: str) -> Dict:
        """组装完整的抽取结果"""
        pipeline_start = time.time()

        # 1. 抽取初始请求
        step_01 = self.extract_initial_request(abstract_intro)

        # 2+3. 合并抽取轨迹与验证结果
        step_02_03 = self.extract_trajectory_and_verification(paper_text, step_01)

        total = time.time() - pipeline_start
        print(f"\n[总耗时] {total:.1f}s")

        # 4. 组装为最终嵌套 JSON 结构
        final_result = {
            "01_initial_request": step_01.model_dump(),
            "02_agent_trajectory": [step.model_dump() for step in step_02_03.steps],
            "03_success_verification": step_02_03.verification.model_dump()
        }

        return final_result

# ==========================================
# 3. 执行测试
# ==========================================
if __name__ == "__main__":
    # 假设你已经通过 PDF 解析工具将论文拆分为了三个字符串
    abstract_text = """
    # Denovo design of luciferases using deep learning

https://doi.org/10.1038/s41586-023-05696-3

Received: 19 January 2022

Accepted: 3 January 2023

Published online: 22 February 2023

Open access

Check for updates

Andy Hsien-Wei Yeh1,2,3,7 ✉, Christoffer Norn1,2,7, Yakov Kipnis1,2,4, Doug Tischer1,2, Samuel J. Pellock1,2, Declan Evans5 , Pengchen Ma5,6, Gyu Rie Lee1,2, Jason Z. Zhang1,2, Ivan Anishchenko1,2, Brian Coventry1,2,4, Longxing Cao1,2, Justas Dauparas1,2, Samer Halabiya2 , Michelle DeWitt2 , Lauren Carter2 , K. N. Houk5 & David Baker1,2,4 ✉

De novo enzyme design has sought to introduce active sites and substrate-binding pockets that are predicted to catalyse a reaction of interest into geometrically compatible native sca"olds1,2 , but has been limited by a lack of suitable protein structures and the complexity of native protein sequence–structure relationships. Here we describe a deep-learning-based ‘family-wide hallucination’ approach that generates large numbers of idealized protein structures containing diverse pocket shapes and designed sequences that encode them. We use these sca"olds to design arti#cial luciferases that selectively catalyse the oxidative chemiluminescence of the synthetic luciferin substrates diphenylterazine3 and 2-deoxycoelenterazine. The designed active sites position an arginine guanidinium group adjacent to an anion that develops during the reaction in a binding pocket with high shape complementarity. For both luciferin substrates, we obtain designed luciferases with high selectivity; the most active of these is a small $( 1 3 . 9 \mathsf { k D a } )$ and thermostable (with a melting temperature higher than $9 5 ^ { \circ } \mathrm { C }$ ) enzyme that has a catalytic e%ciency on diphenylterazine $( k _ { \mathrm { c a t } } / K _ { \mathrm { m } } { = } 1 0 ^ { 6 } \mathsf { M } ^ { - 1 } \mathsf { s } ^ { - 1 } )$ comparable to that of native luciferases, but a much higher substrate speci#city. The creation of highly active and speci#c biocatalysts from scratch with broad applications in biomedicine is a key milestone for computational enzyme design, and our approach should enable generation of a wide range of luciferases and other enzymes.

Bioluminescent light produced by the enzymatic oxidation of a luciferin substrate by luciferases is widely used for bioassays and imaging in biomedical research. Because no excitation light source is needed, luminescent photons are produced in the dark; this results in higher sensitivity than fluorescence imaging in live animal models and in biological samples in which autofluorescence or phototoxicity is a concern4,5 . However, the development of luciferases as molecular probes has lagged behind that of well-developed fluorescent protein toolkits for a number of reasons: (i) very few native luciferases have been identified6,7 ; (ii) many of those that have been identified require multiple disulfide bonds to stabilize the structure and are therefore prone to misfolding in mammalian cells8 ; (iii) most native luciferases do not recognize synthetic luciferins with more desirable photophysical properties9 ; and (iv) multiplexed imaging to follow multiple processes in parallel using mutually orthogonal luciferase– luciferin pairs has been limited by the low substrate specificity of native luciferases10,11.

We sought to use de novo protein design to create luciferases that are small, highly stable, well-expressed in cells, specific for one substrate and need no cofactors to function. We chose a synthetic

luciferin, diphenylterazine (DTZ), as the target substrate because of its high quantum yield, red-shifted emission3 , favourable in vivo pharmacokinetics12,13 and lack of required cofactors for light emission. Previous computational enzyme design efforts have primarily repurposed native protein scaffolds in the Protein Data Bank (PDB)1,2 , but there are few native structures with binding pockets appropriate for DTZ, and the effects of sequence changes on native proteins can be unpredictable (designed helical bundles have also been used as enzyme scaffolds14–16, but these are limited in number and most do not have pockets that are suitable for DTZ binding). To circumvent these limitations, we set out to generate large numbers of small and stable protein scaffolds with pockets of the appropriate size and shape for DTZ, and with clear sequence–structure relationships to facilitate subsequent active-site incorporation. To identify protein folds that are capable of hosting such pockets, we first docked DTZ into 4,000 native small-molecule-binding proteins. We found that many nuclear transport factor 2 (NTF2)-like folds have binding pockets with appropriate shape complementarity and size for DTZ placement (pink dashes in Fig. 1e), and hence selected the NTF2-like superfamily as the target topology.

1 Department of Biochemistry, University of Washington, Seattle, WA, USA. 2 Institute for Protein Design, University of Washington, Seattle, WA, USA. 3 Department of Biomolecular Engineering, University of California, Santa Cruz, CA, USA. 4 Howard Hughes Medical Institute, University of Washington, Seattle, WA, USA. 5 Department of Chemistry and Biochemistry, University of California, Los Angeles, CA, USA. 6 School of Chemistry, Xi’an Key Laboratory of Sustainable Energy Materials Chemistry, MOE Key Laboratory for Nonequilibrium Synthesis and Modulation of Condensed Matter, Xi’an Jiaotong University, Xi’an, China. 7 These authors contributed equally: Andy Hsien-Wei Yeh, Christoffer Norn. ✉e-mail: hsyeh@ucsc.edu; dabaker@uw.edu

![](images/65db30483ce1f72e05979d395607a3b7a43b9a6c5ec7b869a702cf5f7214f1c8.jpg)

![](images/0adf6264e053d2ccdd181e2caa9564d32eb7c3204c281a977c9e184a58631572.jpg)

![](images/bb16b685ef2ede65f425f39f165cc4b216cf763a400cc02c5c12fc236cbfa124.jpg)  
Fig. 1 | Generation of idealized scaffolds and computational design of de novo luciferases. a, Family-wide hallucination. Sequences encoding proteins with the desired topology are optimized by Markov chain Monte Carlo (MCMC) sampling with a multicomponent loss function. Structurally conserved regions (peach) are evaluated on the basis of consistency with input residue–residue distance and orientation distributions obtained from 85 experimental structures of NTF2-like proteins, whereas variable non-ideal regions (teal) are evaluated on the basis of the confidence of predicted inter-residue geometries calculated as the KL divergence between network predictions and the background distribution. The sequence-space MCMC sampling incorporates both sequence changes and insertions and deletions (see Supplementary Methods) to guide the hallucinated sequence towards encoding structures with the desired folds. Hydrogen-bonding networks are incorporated into the designed structures to increase structural specificity. b–d, The design of luciferase active sites. b, Generation of luciferase   
substrate (DTZ) conformers. c, Generation of a Rotamer Interaction Field (RIF) to stabilize anionic DTZ and form hydrophobic packing interactions. d, Docking of the RIF into the hallucinated scaffolds, and optimization of substrate–scaffold interactions using position-specific score matrices (PSSM)- biased sequence design. e, Selection of the NTF2 topology. The RIF was docked into 4,000 native small-molecule-binding proteins, excluding proteins that bind the luciferin substrate using more than five loop residues. Most of the top hits were from the NTF2-like protein superfamily (pink dashes). Using the family-wide hallucination scaffold generation protocol, we generated 1,615 scaffolds and found that these yielded better predicted RIF binding energies than the native proteins. f,g, Our DL-optimized scaffolds sample more within the space of the native structures (f) and have stronger sequence-to-structure relationships (more confident Alphafold2 structure predictions) (g) than native or previous non-deep-learning energy-optimized scaffolds.
    """
    methods_text = """
    # Family-wide hallucination

Native NTF2 structures have a range of pocket sizes and shapes but also contain features that are not ideal, such as long loops that compromise stability. To create large numbers of ideal NTF2-like structures, we developed a deep-learning-based ‘family-wide hallucination’ approach that integrates unconstrained de novo design17,18 and Rosetta sequence-design approaches19 to enable the generation of an essentially unlimited number of proteins that have a desired fold (Fig. 1a). The family-wide hallucination approach used the de novo sequence and structure discovery capability of unconstrained protein hallucination17,18 for loop and variable regions, and structure-guided

sequence optimization for core regions. We used the trRosetta structure prediction neural network20, which is effective in identifying experimentally successful de-novo-designed proteins and hallucinating new globular proteins of diverse topologies. Starting from the sequences of 2,000 naturally occurring NTF2s, we carried out Monte Carlo searches in sequence space, at each step making a sequence change and predicting the structure using trRosetta. As the loss function guiding search, we used the confidence of the neural network in the predicted structure (as in our previous free hallucination study) supplemented with a topology-specific loss function over core residue pair geometries (see Supplementary Methods); in the loop regions, we also allowed the number of residues to vary,

# Article

which resulted in short near ideal loops. To further encode structural specificity, we incorporated buried, long-range hydrogen-bonding networks. The resulting 1,615 family-wide hallucinated NTF2 scaffolds provided more shape-complementary binding pockets for DTZ than did native small-molecule-binding proteins (Fig. 1e). This method samples protein backbones that are closer to native NTF2-like proteins (Fig. 1f) and that have better scaffold quality metrics than those produced in a previous non-deep-learning energy-based approach21 (Fig. 1g).

# De novo design of luciferases for DTZ

Computational enzyme design generally starts from an ideal active site or theozyme consisting of protein functional groups surrounding the reaction transition state that is then matched into a set of existing scaffolds1,2 . However, the detailed catalytic geometry of native marine luciferases is not well understood because only a handful of apo structures and no holo structures with luciferin substrates have been solved (at the time of this study)22–24. Both quantum chemistry calculations25,26 and experimental data27,28 suggest that the chemiluminescent reaction proceeds through an anionic species and that the polarity of the surroundings can substantially alter the free energy of the subsequent single-electron transfer (SET) process with triplet molecular oxygen $( ^ { 3 } \mathbf { O } _ { 2 } )$ . Guided by these data (Extended Data Fig. 1), we sought to design a shape-complementary catalytic site that stabilizes the anionic state of DTZ and lowers the SET energy barrier, assuming that the downstream dioxetane light emitter thermolysis steps are spontaneous. To stabilize the anionic state, we focused on the placement of the positively charged guanidinium group of an arginine residue to stabilize the developing negative charge on the imidazopyrazinone group.

To computationally design such active sites into large numbers of hallucinated NTF2 scaffolds, we first generated an ensemble of anionic DTZ conformers (Fig. 1b). Next, around each conformer, we used the RifGen method29,30 to enumerate rotamer interaction fields (RIFs) on three-dimensional grids consisting of millions of placements of amino acid side chains making hydrogen-bonding and nonpolar interactions with DTZ (Fig.1c). An arginine guanidinium group was placed adjacent to the N1 atom of the imidazopyrazinone group to stabilize the negative charge. RifDock was then used to dock each DTZ conformer and associated RIF in the central cavity of each scaffold to maximize protein–DTZ interactions. An average of eight side-chain rotamers, including an arginine residue to stabilize the anionic imidazopyrazinone core, were positioned in each pocket (Supplementary Fig.2a). For the top 50,000 docks with the most favourable side chain–DTZ interactions, we optimized the remainder of the sequence using RosettaDesign (Fig. 1d) for high-affinity binding to DTZ with a bias towards the naturally observed sequence variation to ensure foldability. During the design process, pre-defined hydrogen-bond networks (HBNets) in the scaffolds were kept intact for structural specificity and stability, and interactions of these HBNet side chains with DTZ were explicitly required in the Rif-Dock step to ensure the preorganization of residues that are essential for catalysis. In the first sequence-design step, the identities of all RIF and HBNet residues were kept fixed, and the surrounding residues were optimized to hold the side chain–DTZ interactions in place and maintain structural specificity. In the second sequence-design step, the RIF residue identities (except the arginine) were also allowed to vary, as Rosetta can identify apolar and aromatic packing interactions that were missed in the RIF owing to binning effects. During sequence design, the scaffold backbone, side chains and DTZ substrate were allowed to relax in Cartesian space. After sequence optimization, the designs were filtered on the basis of ligand-binding energy, protein– ligand hydrogen bonds, shape complementarity and contact molecular surface, and 7,648 designs were selected and ordered as pooled oligos for experimental screening.

# Identification of active luciferases

Oligonucleotides encoding the two halves of each design were assembled into full-length genes and cloned into an Escherichia coli expression vector (see Supplementary Methods). A colony-based screening method was used to directly image active luciferase colonies from the library and the activities of selected clones were confirmed using a 96-well plate expression (Extended Data Fig. 2). Three active designs were identified; we refer to the most active of these as LuxSit (from the Latin lux sit, ‘let light exist’), which at 117 residues (13.9 kDa) is, to our knowledge, smaller than any previously described luciferase. Biochemical analysis, including SDS–PAGE and size-exclusion chromatography (Fig. 2a,b and Extended Data Fig. 3), indicated that LuxSit is highly expressed in E. coli, soluble and monomeric. Circular dichroism (CD) spectroscopy showed a strong far-ultraviolet CD signature, suggesting an organized α-( structure. CD melting experiments showed that the protein is not fully unfolded at $9 5 ^ { \circ } \mathrm { C }$ , and that the full structure is regained when the temperature is dropped (Fig. 2c). Incubation of LuxSit with DTZ resulted in luminescence with an emission peak at around $4 8 0 \mathsf { n m }$ (Fig. 2d), consistent with the DTZ chemiluminescence spectrum. Although we were not able to determine the crystal structure of LuxSit, the structure predicted by AlphaFold2 (ref. 31) is very close to the design model at the backbone level (root-mean-square deviation $( \mathsf { R M S D } ) = 1 . 3 5 \mathring { \mathsf { A } }$ ) and over the side chains interacting with the substrate (Fig. 2e). The designed LuxSit active site contains Tyr14–His98 and Asp18–Arg65 dyads, with the imidazole nitrogen atoms of His98 making hydrogen-bond interactions with Tyr14 and the O1 atom of DTZ (Fig. 2f). The centre of the Arg65 guanidinium cation is 4.2 Å from the N1 atom of DTZ and Asp18 forms a bidentate hydrogen bond to the guanidinium group and backbone N–H of Arg65 (Fig. 2g).

# De novo design of luciferases for h-CTZ

We next sought to apply the knowledge gained from designing LuxSit to create 2-deoxycoelenterazine (h-CTZ)-specific luciferases. Because the molecular shape of h-CTZ is different from that of DTZ, we created an additional set of NTF2 superfamily scaffolds (see Supplementary Methods) with matching pocket shapes and high model confidence (AlphaFold2-predicted local-distance difference test (pLDDT) $> 9 2$ ). We then installed catalytic sites in these scaffolds and designed the first shell-protein side chain–h-CTZ interactions using the histidine and arginine substrate interaction geometries that were most successful in the first round for DTZ. To design the remainder of the sequence, we used ProteinMPNN32, which can result in better stability, solubility and accuracy than RosettaDesign. After filtering on the basis of the AlphaFold2-predicted pLDDT, Cα RMSD, contact molecular surface and Rosetta-computed binding energies (see Supplementary Methods), we selected and experimentally expressed 46 designs in E. coli and identified 2 (HTZ3-D2 and HTZ3-G4) that had luciferase activity with the h-CTZ luciferin substrate. Both designs were highly soluble, monodisperse and monomeric, and the luciferase activities were of the same order of magnitude as LuxSit (Extended Data Fig. 4). The success rate increased from 3/7,648 to 2/46 sequences in the second round, probably owing to the knowledge of active-site geometry from the first round and the increased robustness of the ProteinMPNN method of sequence design.

# Optimization of luciferase activity

To better understand the contributions to the catalysis of LuxSit, the most active of our designs, we constructed a site-saturation mutagenesis (SSM) library in which each residue in the substrate-binding pocket was mutated to every other amino acid one at a time (see Supplementary Methods), and determined the effect of each mutation onluciferase activity. Figure2f–i shows the amino acid preferences at key positions.

![](images/e6239bcac6e7e8d4fdf4a6fc0fa6e829fe70c41071bbd887b2085a34fd1235c6.jpg)  
a

![](images/1782299f0a80ed8b5412f9a2981f4716c695d808521afff8f00092ea92438f14.jpg)  
b

![](images/df4075474de088d83152d8887c75c9a6f2ab99d209ed51066e5bef1cc57e41b6.jpg)  
d

![](images/1d6268bf27a70209abca6b591248f649047881e0d9df51c97ec2c6d3fbd391b2.jpg)

![](images/a51597fa95113b6ca298990090fb7c94ff04c3d893c373e25a9c7051ace6663d.jpg)  
e

![](images/1fd3643f75a0186afc7d68e08154bd8b15939ef724a9da440950700722b8af51.jpg)  
f

![](images/3488afa377ac94623da2d322250866d0bd6e06e4ea8b686472f29d1c57827a46.jpg)

![](images/75bdc62dbf80300053e62f3462d740d9fe36c672204e1eb8db72967de4797666.jpg)  
h

![](images/45ab771b15b8a0895146bbc9d5fbf1c81962bf65ab403333b0f339f93a6f4d0b.jpg)

![](images/49985d3aea61f4407c2f7e5e8d020557f393dba257a059b1ef16393749b070d3.jpg)

![](images/711d9f57345a6a7f89ceeb8d21c22767482704362371a1971cecaa3a4d910f1c.jpg)

![](images/c1a462162c2d72ec58e0e5fa807afff7cf6af6d9e19e20184d30723f843cae33.jpg)  
i

![](images/6a83786fa6265b78fc62b04ca7ca19dc44e3ca2fabb11c008bc0036a8f8cfbec.jpg)  
Fig. 2 | Biophysical characterization of LuxSit. a, Coomassie-stained SDS–PAGE of purified recombinant LuxSit from E. coli (for gel source data, see Supplementary Fig. 1). b, Size-exclusion chromatography of purified LuxSit suggests monodispersed and monomeric properties. c, Far-ultraviolet CD spectra at $2 5 ^ { \circ } \mathrm { C }$ (black), $9 5 ^ { \circ } \mathrm { C }$ (red) and cooled back to $2 5 ^ { \circ } \mathrm { C }$ (green). Insert, CD melting curve of LuxSit at $2 2 0 \mathsf { n m }$ . MRE, molar residue ellipticity. d, Luminescence emission spectra of DTZ in the presence (blue) and absence (green) of LuxSit. e, Structural alignment of the design model (blue) and AlphaFold2-predicted model (grey), which are in close agreement at both the backbone (left) and the   
side-chain (right) level. f–i, Site-saturation mutagenesis of substrate-interacting residues. Magnified views (left) of designed (blue) and AlphaFold2 (grey) models at the side-chain level, illustrating the designed enzyme–substrate interactions of Tyr14–His98 core HBNet (f), Asp18–Arg65 dyad (g), )-stacking (h) and hydrophobic packing (i) residues. Sequence profiles (right) are scaled by the activities of different sequence variants: (activity for the indicated amino acid)/(sum of activities over all tested amino acids at the indicated position). A96M and M110V substitutions with increased activity are highlighted in pink.

Arg65 is highly conserved (Fig. 2g), and its dyad partner Asp18 can only be mutated to Glu (which reduces activity), suggesting that the carboxylate–Arg65 hydrogen bond is important for luciferase activity. In the Tyr14–His98 dyad (Fig. 2f), Tyr14 can be substituted with Asp and Glu, and His98 can be replaced with Asn. As all active variants had hydrogen-bond donors and acceptors at these positions, the dyads might help to mediate the electron and proton transfer required for luminescence. Hydrophobic (Fig. 2i) and )-stacking (Fig. 2h) residues at the binding interface tolerate other aromatic or aliphatic substitutions and generally prefer the amino acid in the original design, consistent with model-based affinity predictions of mutational effects (Extended Data Fig.5). The A96M and M110V mutants (highlighted in pink) increase activity by 16-fold and 19-fold, respectively, over LuxSit (Supplementary

Table 1). Optimization guided by these results yielded LuxSit-f (A96M/ M110V), with a flash-type emission kinetic, and LuxSit-i (R60S/A96L/ M110V), with a photon flux more than 100-fold higher than that of LuxSit (Extended Data Fig. 6). Overall, the active-site-saturation mutagenesis results support the design model, with the Tyr14–His98 and Asp18– Arg65 dyads having key roles in catalysis and the substrate-binding pocket largely conserved.

The most active catalysts, LuxSit-i (Extended Data Fig. 3b,e,h) and LuxSit-f (Extended Data Fig. 3c,f,i), were both expressed solubly in E. coli at high levels and are monomeric (some dimerization was observed at the high protein concentration; Extended Data Fig. 3l) and thermostable (Extended Data Fig. 3j,k). Similar to native luciferases that use CTZ, the apparent Michaelis constants $( K _ { \mathfrak { m } } )$ of both LuxSit-i and

![](images/433a774375c1703734461b02193ef33b9df65659b1f04b42026b926bb2ad27d8.jpg)

![](images/20c0e4db7a32aabc5620ee1e898f0449d8122ec38f9064b0414d0b2b012283a0.jpg)  
b

![](images/2b351b998596d42dd1ac32c15dc5c48e91112636273621549dc93d8aca2c5871.jpg)  
Fig. 3 | Characterization of de novo luciferase activity in vitro and in human cells. a, Substrate concentration dependence of LuxSit, LuxSit-f and LuxSit-i activity. Numbers indicate the signal-to-background (S/N) ratio at $V _ { \mathrm { m a x } }$ (photon s−1 molecule−1 ). Data are mean ± s.d. $\left( n = 3 \right)$ . b, Luminescence images acquired by a BioRad Imager (top) or an Apple iPhone 8 camera (bottom). Tubes from left to right: DTZ only; DTZ plus 100 nM purified LuxSit; and DTZ plus 100 nM purified LuxSit-i, showing the high efficiency of photon production. c, Fluorescence and luminescence microscopic images of live HEK293T cells transiently expressing LuxSit-i-mTagBFP2; LuxSit-i activity can be detected at single-cell resolution. Left, fluorescence channel representing the mTagBFP2 signal. Right, total luminescence photons were collected during a course of a 10-s exposure without excitation light, immediately after adding $2 5 \mu \mathrm { M }$ DTZ. Insets, negative control, untransfected cells with DTZ. Scale bars, $2 0 \mu \mathrm { m } ; 4 0 \times$ magnification.

LuxSit-f are in the low-micromolar range (Fig. 3a) and the luminescent signal decays over time owing to fast catalytic turnover (Extended Data Fig. 7a). LuxSit-i is a very efficient enzyme, with a catalytic efficiency $( k _ { \mathrm { { c a t } } } / K _ { \mathrm { { m } } } )$ of $1 0 ^ { 6 } \mathsf { M } ^ { - 1 } \mathsf { s } ^ { - 1 }$ . The luminescence signal is readily visible to the naked eye (Fig.3b), and the photon flux (photons per second) is $3 8 \%$ greater than that of the native Renilla reniformis luciferase (RLuc) (Supplementary Table 2). The DTZ luminescent reaction catalysed by LuxSit-i is pH-dependent (Extended Data Fig. 7b), consistent with the proposed mechanism. We used a combination of density functional theory (DFT) calculations and molecular dynamics (MD) simulations to investigate the basis for LuxSit activity in more detail; the results support the anion-stabilization mechanism (Extended Data Fig. 8a and Supplementary Fig. 3a) and suggest that LuxSit-i provides better DTZ transition-state charge stabilization than LuxSit (Extended Data Fig. 8b).

# Cell imaging and multiplexed bioassay

As luciferases are commonly used genetic tags and reporters for cell biological studies, we evaluated the expression and function of LuxSit-i in live mammalian cells. HEK293T cells expressing LuxSit-i-mTagBFP2

showed DTZ-specific luminescence (Fig. 3c), which was maintained after targeting of LuxSit-i-mTagBFP2 to the nucleus, membrane and mitochondria (Extended Data Fig.9). Native and previously engineered luciferases are quite promiscuous, with activity on many luciferin substrates (Fig. 4ac and Supplementary Fig. 4); this is possibly a result of their large and open pockets (a luciferase with high specificity to one luciferin substrate has been difficult to control even with extensive directed evolution33,34). By contrast, LuxSit-i exhibited exquisite specificity for its target luciferin, with 50-fold selectivity for DTZ over bis-CTZ (which differs only in one benzylic carbon; MD simulations suggest that this arises from greater transition-state shape complementarity (Extended Data Fig. 8b,c and Supplementary Fig. 3b,c)), 28-fold selectivity over 8pyDTZ (differing only in one nitrogen atom) and more than 100-fold selectivity over other luciferin substrates (Fig. 4b). One of our active design for h-CTZ (HTZ3-G4) was also highly specific for its target substrate (Fig. 4c and Extended Data Fig. 4d). Overall, the specificity of our designed luciferases is much greater than that of native luciferases35,36 or previously engineered luciferases37 (Supplementary Table 5).

We reasoned that the high substrate specificity of LuxSit-i could allow the multiplexing of luminescent reporters through substrate-specific or spectrally resolved luminescent signals (Fig.4d and Extended Data Fig. 10a,b). To investigate this possibility, we placed LuxSit-i downstream of the NF- $\mathbf { \kappa } _ { \mathbf { K } } \mathbf { B }$ response element and RLuc downstream of the cAMP response element (Fig. 4e). The addition of activators (TNF) of the NF-κB signaling pathway resulted in luminescence when cells were incubated with DTZ, while the luminescence of PP-CTZ (the substrate of RLuc) was observed only when the cAMP–PKA pathway was activated (Fig. 4f). Because DTZ and PP-CTZ emit luminescence at different wavelengths, they can in principle be combined and the two signals can be deconvoluted through spectral analysis. Indeed, we observed that activating the NF-κB signaling resulted in luminescence at theDTZ wavelength,while the addition of cAMP–PKA pathway activators (FSK) generated luminescence at the PP-CTZ wavelength, allowing us to simultaneously assess the activation of the two signaling pathways in the same sample with either cell lysates (Fig. 4g) or intact HEK293T cells (Extended Data Fig.10c–e) by providing both substrates together. Thus, the high substrate specificity of LuxSit-i enables multiplexed reporting of diverse cellular responses.

# Conclusion

Computational enzyme design has been constrained by the number of available scaffolds, which limits the extent to which catalytic configurations and enzyme–substrate shape complementarity can be achieved14–16. The use of deep learning to produce large numbers of de-novo-designed scaffolds here eliminates this restriction, and the more accurate RoseTTAfold (ref. 38) and AlphaFold2 (ref. 31) should enable protein scaffolds to be generated even more effectively through family-wide hallucination and other approaches18,39. The diversity of shapes and sizes of scaffold pockets enabled us to consider a range of catalytic geometries and to maximize reaction intermediate–enzyme shape complementarity; to our knowledge, no native luciferases have folds similar to LuxSit, and the enzyme has high specificity for a fully synthetic luciferin substrate that does not exist in nature. With the incorporation of three substitutions that provide a more complementary pocket to stabilize the transition state, LuxSit-i has higher activity than any previous de-novo-designed enzyme, with a $k _ { \mathrm { c a t } } / K _ { \mathrm { m } } ( 1 0 ^ { 6 } \mathsf { M } ^ { - 1 } \mathsf { s } ^ { - 1 } )$ in the range of native luciferases. This is a notable advance for computational enzyme design, as tens of rounds of directed evolution were required to obtain catalytic efficiencies in this range for a designed retroaldolase, and the structure was remodelled considerably40; by contrast, the predicted differences in ligand–side-chain interactions between LuxSit and LuxSit-i are very subtle (Supplementary Fig. 2b; achieving such high activities directly from the computer remains a

![](images/1be4526b41a634fdf36e57443cfdf017f34512e916f507c0c8fdfda3dff34d85.jpg)

![](images/18c17203bded67369d473ce9166ffa6add4e0a59a9f4d0ba5c11dba136a15f88.jpg)

![](images/8180193d499a2f24926b3eedb5e7160728be4549b5723f4eb9fd1458d1c03e53.jpg)

![](images/4d94cb1c966358fb078410c726b0d7632d297d69fd684285ca1f2f230dda3d8f.jpg)

![](images/2eab382cf3cc37ab2f684a02e529fdda2b53111fde23a4465ddedf7745d2e559.jpg)

![](images/938418be3153b3720a4f92c4c5c90b806e349e8db3b6ae2278306a13c4fac817.jpg)

![](images/3dd43d1432360fda2538545ae16d0a7ee8cc6520988763a3609fe7180f54f575.jpg)  
Fig. 4 | High substrate specificity of de novo luciferases allows multiplexed bioassay. a, Chemical structures of coelenterazine substrate analogues. b, Normalized activity of LuxSit-i on selected luciferin substrates. Luminescence image (top) and signal quantification (bottom) of the indicated substrate in the presence of 100 nM LuxSit-i. LuxSit-i has high specificity for the design target substrate, DTZ. c, Heat map visualization of the substrate specificity of LuxSit-i; Renilla luciferase (RLuc); Gaussia luciferase (GLuc); engineered NLuc from Oplophorus luciferase; and the de novo luciferase (HTZ3-G4) designed for h-CTZ. The heat map shows the luminescence for each enzyme on each substrate; values are normalized on a per-enzyme basis to the highest signal for that enzyme over all substrates. d, The luminescence emission spectrum of LuxSit-i-DTZ (green) and RLuc-PP-CTZ (purple) can be spectrally resolved by 528/20 and 390/35 filters (shown in dashed bars) and only recognize the cognate substrate. e, Schematic of the multiplex luciferase assay. HEK293T cells

challenge in computational enzyme design). The small size, stability and robust folding of LuxSit-i makes it well-suited in luciferase fusions to proteins of interest and as a genetic tag for capacity-limited viral vectors. On the basic science side, the small size, simplicity and high activity of LuxSit-i make it an excellent model system for computational and experimental studies of luciferase catalytic mechanism. Extending the approach used here to create similarly specific luciferases for synthetic luciferin substrates beyond DTZ and h-CTZ would considerably extend the multiplexing opportunities illustrated in Fig. 4 (particularly with the recent advances in microscopy41), and enable a new generation of multiplexed luminescent toolkits. More generally, our family-wide hallucination method opens up an almost unlimited number of scaffold possibilities for substrate binding and catalytic residue placement, which is particularly important when the reaction mechanism and how to promote it are not completely understood: many alternativestructural and catalytic hypotheses can be readily enumerated with

transiently transfected with CRE-RLuc, NF-κB-LuxSit-i and CMV-CyOFP plasmids were treated with either forskolin (FSK) or human tumour necrosis factor (TNF) to induce the expression of labelled luciferases. f,g, Luminescence signals from cells can be measured under either substrate-resolved or spectrally resolved methods by a plate reader. f, For the substrate-resolved method, luminescence intensity was recorded without a filter after adding either PP-CTZ or DTZ. g, For the spectrally resolved method, both PP-CTZ and DTZ were added, and the signals were acquired using 528/20 and 390/35 filters simultaneously. In f and g, the bottom panel indicates the addition of FSK or TNF. Luminescence signals were acquired from the lysate of 15,000 cells in CelLytic M reagent, and the CyOFP fluorescence signal was used to normalize cell numbers and transfection efficiencies. All data were normalized to the corresponding non-stimulated control. Data are mean ± s.d. $\scriptstyle ( n = 3 )$ ).

shape and chemically complementary binding pockets but different catalytic residue placements. Although luciferases are unique in catalysing the emission of light, the chemical transformation of substrates into products is common to all enzymes, and the approach developed here should be readily applicable to a wide variety of chemical reactions.
    """
    extractor = ProteinPaperExtractor()
    try:
        final_json = extractor.run_pipeline(abstract_text, methods_text)
        print("\n=== 最终组装的抽取结果 ===")
        print(json.dumps(final_json, indent=2, ensure_ascii=False))

        output_path = "result.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(final_json, f, indent=2, ensure_ascii=False)
        print(f"\n结果已保存至 {output_path}")
    except Exception as e:
        print(f"Pipeline 运行出错: {e}")