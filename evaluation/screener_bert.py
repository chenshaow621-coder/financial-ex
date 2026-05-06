# evaluation/screener_bert.py
import pandas as pd
from sentence_transformers import SentenceTransformer, util
import torch


class BertScreener:
    def __init__(self, model_name='shibing624/text2vec-base-chinese'):
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Loading BERT model on {device}...")
        self.model = SentenceTransformer(model_name, device=device)

    def calculate_similarity(self, df):
        """
        计算 BERT 相似度并返回带有分数的 DataFrame
        """
        # 1. 构造还原文本
        fields = ['who', 'when', 'where', 'how', 'what']
        for col in fields:
            df[col] = df[col].fillna('').astype(str)

        df['reconstructed_text'] = df[fields].agg(' '.join, axis=1)
        df['content_original'] = df['content_original'].fillna('').astype(str)

        # 2. 批量计算向量
        embeddings_orig = self.model.encode(df['content_original'].tolist(), convert_to_tensor=True)
        embeddings_recon = self.model.encode(df['reconstructed_text'].tolist(), convert_to_tensor=True)

        # 3. 计算余弦相似度
        cosine_scores = util.cos_sim(embeddings_orig, embeddings_recon)
        # 提取对角线分数
        df['bert_score'] = torch.diag(cosine_scores).cpu().tolist()

        return df