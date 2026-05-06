# evaluation/pipeline.py
import pandas as pd
import os
import config
from screener_bert import BertScreener
from judge_llm import QwenJudge
from tqdm import tqdm


def read_data_smart(path):
    # (复用之前的智能读取逻辑，略)
    if path.endswith('.xlsx'): return pd.read_excel(path)
    return pd.read_csv(path, encoding='gb18030')


def main():
    print(">>> 1. 启动数据加载...")
    df = read_data_smart(config.INPUT_FILE)

    # --- 阶段一：BERT 粗筛 ---
    print(">>> 2. 执行 BERT 语义筛选 (Screening)...")
    screener = BertScreener()
    df = screener.calculate_similarity(df)

    # --- 阶段二：漏斗分流 ---
    # 定义分流逻辑
    # High Confidence Pass: Score > 0.92 (认为是安全的)
    # Low Quality Fail: Score < 0.60 (认为是垃圾数据)
    # Ambiguous Zone: 0.60 <= Score <= 0.92 (需要 LLM 介入)

    mask_check = (df['bert_score'] >= config.THRESHOLD_LOW) & (df['bert_score'] <= config.THRESHOLD_HIGH)
    check_indices = df[mask_check].index

    print(f">>> 3. 启动 LLM 逻辑复验 (共 {len(check_indices)} 条数据落入复验区)...")

    judge = QwenJudge()

    # 初始化结果列
    df['final_status'] = 'PENDING'
    df['logic_reason'] = ''

    # 自动标记不需要 LLM 跑的数据
    df.loc[df['bert_score'] > config.THRESHOLD_HIGH, 'final_status'] = 'AUTO_PASS'
    df.loc[df['bert_score'] < config.THRESHOLD_LOW, 'final_status'] = 'AUTO_FAIL'

    # 对复验区数据跑 Qwen
    # 使用 tqdm 显示进度条
    results = []
    for idx in tqdm(check_indices, desc="Qwen Judging"):
        row = df.loc[idx]
        res = judge.evaluate_logic(row['content_original'], row['reconstructed_text'])

        # 写入结果
        df.at[idx, 'final_status'] = 'LLM_PASS' if res['is_pass'] else 'LLM_FAIL'
        df.at[idx, 'logic_reason'] = f"[{res['risk_type']}] {res['reason']}"

    # --- 阶段三：输出报告 ---
    print(">>> 4. 生成最终报告...")
    df.to_csv(config.OUTPUT_FILE, index=False, encoding='utf_8_sig')

    # 打印统计
    print("\n=== 最终评估摘要 ===")
    print(df['final_status'].value_counts())
    print(f"结果已保存至: {config.OUTPUT_FILE}")


if __name__ == "__main__":
    main()