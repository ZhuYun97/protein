import re
from typing import List
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

# ==========================================
# 步骤 1: 使用 LangChain 实现 Chunking
# ==========================================
def chunk_texts_with_langchain(markdown_text: str) -> List[Document]:
    """
    对 MinerU 解析的 Markdown 文本进行分层切分
    """
    # 1. 按照 Markdown 标题切分，保留章节层级作为 Metadata
    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    md_splits = markdown_splitter.split_text(markdown_text)

    # 2. 对长文本块进行字符级滑动窗口切分
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,       # 限制每个块的大小 400
        chunk_overlap=50,     # 设置 50 个字符的重叠，防止关键句子被硬截断
        separators=["\n\n", "\n", ".", " "]
    )
    
    # split_documents 会保留上一步生成的 metadata
    final_chunks = text_splitter.split_documents(md_splits)
    
    # 为了后续能检索相邻的 Chunk，我们向 metadata 中注入全局的序号索引
    for i, doc in enumerate(final_chunks):
        doc.metadata["chunk_index"] = i
        
    return final_chunks


# ==========================================
# 步骤 2: 针对特定工具名检索相关的 Chunks (带上下文扩展)
# ==========================================
def retrieve_tool_context(tool_name: str, chunks: List[Document], window_size: int = 1) -> str:
    """
    检索包含工具名的 Document，并自动合并其前后相邻的 Document 以保证参数不丢失。
    """
    hit_indices = set()
    
    # 1. 查找包含工具名称的 chunk 索引（忽略大小写）
    pattern = re.compile(re.escape(tool_name), re.IGNORECASE)
    for doc in chunks:
        if pattern.search(doc.page_content):
            hit_indices.add(doc.metadata["chunk_index"])
            
    if not hit_indices:
        return f"未找到提及工具 '{tool_name}' 的相关内容。"
        
    # 2. 扩展上下文窗口 (获取相邻的 chunks)
    merged_indices = set()
    for idx in hit_indices:
        start_idx = max(0, idx - window_size)
        end_idx = min(len(chunks) - 1, idx + window_size)
        for j in range(start_idx, end_idx + 1):
            merged_indices.add(j)
            
    # 3. 组装最终送给大模型抽取的文本
    sorted_indices = sorted(list(merged_indices))
    
    retrieved_context = f"--- 工具 [{tool_name}] 的相关上下文 ---\n"
    for idx in sorted_indices:
        doc = chunks[idx]
        # 提取 Metadata 里的章节信息
        headers = [val for key, val in doc.metadata.items() if key.startswith("Header")]
        header_path = " > ".join(headers) if headers else "未知章节"
        
        retrieved_context += f"\n[来源: {header_path} | Chunk {idx}]\n{doc.page_content}\n"
        
    return retrieved_context

# ==========================================
# 运行测试
# ==========================================
if __name__ == "__main__":
    # 模拟 MinerU 解析出的文本
    sample_mineru_text = """
    ## 3. Method
    
    ### 3.1 Model Architecture
    In this section, we describe the network. We used RFdiffusion to generate the initial backbones. 
    The network is robust and highly efficient for this task.
    
    The sampling steps were set to 50, and we applied a secondary structure constraint. Noise scale was 0.8.
    
    ### 3.2 Sequence Design
    Next, sequences were generated using ProteinMPNN. We used a sampling temperature of 0.1.
    """

    # 1. 执行 Chunking
    langchain_chunks = chunk_texts_with_langchain(sample_mineru_text)
    
    print(f"总共生成了 {len(langchain_chunks)} 个 Chunks。\n")

    # 2. 检索并扩展 RFdiffusion 的上下文
    tool_query = "RFdiffusion"
    context_for_llm = retrieve_tool_context(tool_query, langchain_chunks, window_size=1)
    
    print(context_for_llm)