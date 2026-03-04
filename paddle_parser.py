from paddleocr import PaddleOCRVL
from pathlib import Path
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

input_file = "/mnt/dhwfile/raise/user/zhuyun/protein_data_pipeline/input_pdfs/case_pdfs/case1.pdf"
output_path = Path("./paddle_parsed_output")

pipeline = PaddleOCRVL()

output = pipeline.predict(input=input_file)

pages_res = list(output)

# output = pipeline.restructure_pages(pages_res)

# output = pipeline.restructure_pages(pages_res, merge_tables=True) # 合并跨页表格
# output = pipeline.restructure_pages(pages_res, merge_tables=True, relevel_titles=True) # 合并跨页表格，重建多级标题
output = pipeline.restructure_pages(pages_res, merge_tables=True, relevel_titles=True, concatenate_pages=True) # 合并跨页表格，重建多级标题，合并多页结果为一页

for res in output:
    res.print() ## 打印预测的结构化输出
    res.save_to_json(save_path="./parsed_outputs/paddlevl1.5/case1") ## 保存当前图像的结构化json结果
    res.save_to_markdown(save_path="./parsed_outputs/paddlevl1.5/case1") ## 保存当前图像的markdown格式的结果