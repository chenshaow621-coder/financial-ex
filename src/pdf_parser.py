import pdfplumber
import os
import logging
import sys

# === 🛠️ 终极防御：在导入任何深度学习库之前，先设置环境变量 ===
# 1. 强制使用 CPU (防止 CUDA 报错)
os.environ["CUDA_VISIBLE_DEVICES"] = ""
# 2. 解决 OpenMP 库冲突 (这是导致 0xC0000005 的常见原因)
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# 3. 禁用 MKLDNN 加速 (防止指令集报错)
os.environ["FLAGS_use_mkldnn"] = "0"
# 4. 禁用 Paddle 调试日志
os.environ["GLOG_minloglevel"] = "2"

# 尝试导入 OCR 库
try:
    from paddleocr import PaddleOCR

    HAS_OCR = True
    # 压制日志
    logging.getLogger("ppocr").setLevel(logging.ERROR)
except ImportError:
    HAS_OCR = False
    PaddleOCR = None


class LegalPDFParser:
    def __init__(self, file_path):
        self.file_path = file_path
        self.ocr_engine = None

    def _get_ocr_engine(self):
        if self.ocr_engine is None and HAS_OCR:
            print("🔧 正在初始化 OCR 引擎 (稳定模式)...")
            try:
                # === 🔴 修改策略：什么参数都不传，全靠环境变量控制 ===
                # lang="ch": 中英文
                # use_angle_cls=True: 纠正方向
                # ocr_version="PP-OCRv4": 强制指定使用 v4 模型 (比 v5 稳定)
                self.ocr_engine = PaddleOCR(
                    use_angle_cls=True,
                    lang="ch",
                    ocr_version="PP-OCRv4",
                    show_log=False
                )
            except Exception as e:
                print(f"⚠️ OCR 初始化失败: {e}")
                # 最后一搏：如果不带参数也不行，就彻底放弃 OCR，防止程序崩溃
                self.ocr_engine = None

        return self.ocr_engine

    def parse(self):
        print(f"📄 正在解析 PDF: {os.path.basename(self.file_path)}...")
        results = []

        try:
            with pdfplumber.open(self.file_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_num = i + 1

                    # 1. 提取文本
                    text = page.extract_text() or ""

                    # 2. 如果是扫描件，启动 OCR
                    if len(text.strip()) < 50:
                        if not HAS_OCR:
                            print(f"     ⚠️ 第 {page_num} 页疑似扫描件，但未安装 paddleocr，跳过。")
                            continue

                        ocr = self._get_ocr_engine()
                        if ocr:
                            print(f"     👁️ 第 {page_num} 页识别为扫描件，正在进行 OCR 识别...")
                            try:
                                # 转换图片
                                im_obj = page.to_image(resolution=300)
                                import numpy as np
                                img_array = np.array(im_obj.original)

                                # 识别
                                ocr_result = ocr.ocr(img_array, cls=True)

                                # 拼接
                                ocr_text_list = []
                                if ocr_result and ocr_result[0]:
                                    for line in ocr_result[0]:
                                        ocr_text_list.append(line[1][0])

                                text = "\n".join(ocr_text_list)
                                print(f"     ✅ OCR 成功提取 {len(text)} 个字符")
                            except Exception as e:
                                print(f"     ❌ OCR 识别过程出错: {e}")
                        else:
                            print(f"     ⚠️ OCR 引擎不可用，跳过此页。")

                    # 3. 保存结果
                    if len(text.strip()) > 20:
                        lines = text.split('\n')
                        clean_lines = [line.strip() for line in lines if len(line.strip()) > 1]
                        page_content = "\n".join(clean_lines)

                        results.append({
                            "page_num": page_num,
                            "content": page_content,
                            "source_file": os.path.basename(self.file_path)
                        })
                    else:
                        print(f"     ⚠️ 第 {page_num} 页内容过少，已跳过。")

        except Exception as e:
            print(f"❌ PDF 解析崩溃: {e}")
            return []

        return results


if __name__ == "__main__":
    # 测试路径
    test_path = "../data/raw/中国人民银行关于取消企业银行账户许可的通知（银发〔2019〕41号）.pdf"
    if os.path.exists(test_path):
        parser = LegalPDFParser(test_path)
        pages = parser.parse()
        print(f"\n✅ 测试结果：共识别到 {len(pages)} 页")