import pandas as pd
import numpy as np
from sentence_transformers import SentenceTransformer, util
import torch
import os
import matplotlib.pyplot as plt
import seaborn as sns

# =================配置区域=================
# 输入文件名 (请修改为你实际的文件名)
INPUT_FILE = 'legal_atoms_v4_final.xlsx'
# 输出文件名
OUTPUT_CSV = 'legal_atoms_evaluated.csv'
OUTPUT_IMG = 'evaluation_report.png'

# 模型选择
MODEL_NAME = 'shibing624/text2vec-base-chinese'
# =========================================

# 设置绘图风格
sns.set(style="whitegrid")
# 解决 Matplotlib 中文乱码问题 (尝试自动寻找支持中文的字体)
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'Microsoft YaHei', 'SimSun', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False


def read_data_smart(file_path):
    """智能读取函数：自动处理格式和编码"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"❌ 找不到文件: {file_path}")

    if file_path.lower().endswith(('.xlsx', '.xls')):
        print(f"📂 检测到 Excel 文件，正在读取...")
        return pd.read_excel(file_path)
    else:
        print(f"📂 检测到 CSV 文件，正在读取...")
        try:
            return pd.read_csv(file_path, encoding='gb18030')
        except:
            return pd.read_csv(file_path, encoding='utf-8', errors='replace')


def plot_results(df, score_col='bert_similarity_score', output_path='evaluation_report.png'):
    """生成可视化报表"""
    print("📊 正在生成可视化图表...")

    plt.figure(figsize=(16, 7))

    # 1. 分数分布直方图 (Histogram)
    plt.subplot(1, 2, 1)
    # 绘制直方图和密度曲线
    sns.histplot(df[score_col], bins=30, kde=True, color='#3498db', edgecolor='white', alpha=0.8)

    # 画辅助线
    mean_score = df[score_col].mean()
    threshold = 0.85
    plt.axvline(mean_score, color='green', linestyle='--', linewidth=2, label=f'平均分: {mean_score:.2f}')
    plt.axvline(threshold, color='red', linestyle=':', linewidth=2, label=f'优秀线: {threshold}')

    plt.title('原子规则抽取质量分布 (BERT语义相似度)', fontsize=16, fontweight='bold')
    plt.xlabel('相似度得分 (0-1)', fontsize=12)
    plt.ylabel('规则数量', fontsize=12)
    plt.legend()

    # 2. 质量等级饼图 (Pie Chart)
    plt.subplot(1, 2, 2)

    # 定义等级
    def get_grade(score):
        if score >= 0.85:
            return '完美 (Perfect)'
        elif score >= 0.6:
            return '良好 (Good)'
        else:
            return '需人工复核 (Check)'

    grades = df[score_col].apply(get_grade).value_counts()

    # 颜色映射
    colors = ['#2ecc71', '#f1c40f', '#e74c3c']  # 绿、黄、红

    plt.pie(grades, labels=grades.index, autopct='%1.1f%%', startangle=140, colors=colors,
            textprops={'fontsize': 12}, explode=[0.05] * len(grades))
    plt.title('整体抽取质量占比', fontsize=16, fontweight='bold')

    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    print(f"✅ 图表已保存至: {output_path}")


def main():
    # 1. 加载模型
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"🚀 正在加载模型: {MODEL_NAME} (使用设备: {device})...")
    model = SentenceTransformer(MODEL_NAME, device=device)

    # 2. 读取数据
    df = read_data_smart(INPUT_FILE)
    print(f"✅ 成功加载 {len(df)} 条数据")

    # 3. 预处理
    fields = ['who', 'when', 'where', 'how', 'what']
    for col in fields:
        if col not in df.columns: df[col] = ''  # 缺列补全
        df[col] = df[col].fillna('').astype(str)

    if 'content_original' not in df.columns:
        raise ValueError("数据缺少 'content_original' 列")
    df['content_original'] = df['content_original'].fillna('').astype(str)

    # 构造还原文本
    df['reconstructed_text'] = df[fields].agg(' '.join, axis=1)

    # 4. 计算相似度
    print("⚡ 正在计算语义向量 (这可能需要一点时间)...")
    embeddings_original = model.encode(df['content_original'].tolist(), convert_to_tensor=True, show_progress_bar=True)
    embeddings_reconstructed = model.encode(df['reconstructed_text'].tolist(), convert_to_tensor=True,
                                            show_progress_bar=True)

    cosine_scores = util.cos_sim(embeddings_original, embeddings_reconstructed)
    df['bert_similarity_score'] = torch.diag(cosine_scores).cpu().tolist()

    # 5. 输出统计信息
    avg_score = df['bert_similarity_score'].mean()
    perfect_rate = (df['bert_similarity_score'] >= 0.85).mean() * 100

    print("\n" + "=" * 30)
    print(f"   🎯 最终评估结果")
    print("=" * 30)
    print(f"平均得分: {avg_score:.4f}")
    print(f"优秀率 (>0.85): {perfect_rate:.2f}%")
    print("-" * 30)

    # 6. 保存与绘图
    df.to_csv(OUTPUT_CSV, index=False, encoding='utf_8_sig')
    print(f"💾 数据已保存: {OUTPUT_CSV}")

    plot_results(df, score_col='bert_similarity_score', output_path=OUTPUT_IMG)

    # 展示低分样本供分析
    print("\n🔍 [建议复核] 最低分样本 TOP 3:")
    low_scores = df.nsmallest(3, 'bert_similarity_score')[
        ['content_original', 'reconstructed_text', 'bert_similarity_score']]
    for i, (idx, row) in enumerate(low_scores.iterrows()):
        print(f"{i + 1}. [得分: {row['bert_similarity_score']:.3f}]")
        print(f"   原文: {row['content_original'][:50]}...")
        print(f"   还原: {row['reconstructed_text'][:50]}...\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"\n❌ 程序发生错误: {e}")
        print("💡 提示: 请检查文件名是否正确，或者是否安装了 matplotlib 和 seaborn")