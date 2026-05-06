# evaluation/config.py

# === 路径配置 ===
INPUT_FILE = '../data/processed/legal_atoms_v4_final.xlsx'  # 根据你的实际位置调整
OUTPUT_FILE = '../data/processed/legal_atoms_evaluated_final.csv'

# === BERT 筛选阈值 ===
# 分数低于此值 -> 直接判定为“抽取失败” (Bad Case)
THRESHOLD_LOW = 0.60
# 分数高于此值 -> 直接判定为“通过” (Pass)，无需浪费 LLM Token
THRESHOLD_HIGH = 0.92

# === LLM 配置 (Qwen) ===
# 请替换为你的阿里云 DashScope API Key
DASHSCOPE_API_KEY = "sk-b270d38e27ca42eabec6c16efe27b385"
LLM_MODEL = "qwen-plus"  # 推荐使用 qwen-plus 或 qwen-max 以保证逻辑判断能力