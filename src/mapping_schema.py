# src/mapping_schema.py
from pydantic import BaseModel, Field
from typing import Literal, Optional

class TermMapping(BaseModel):
    legal_term: str = Field(..., description="法规原文中的业务名词，如'票据权利人'")
    domain_mapping: str = Field(..., description="映射到的系统实体.字段，如'Bill.holder_id'")
    mapping_logic: str = Field(..., description="具体的转换逻辑或条件，如 'Client.type == ENTERPRISE'")
    confidence: str = Field(..., description="置信度 (High/Medium/Low)")