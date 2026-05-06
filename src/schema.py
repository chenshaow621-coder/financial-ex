from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List
from enum import Enum

# ==========================================
# 1. 核心实体类型枚举 (4W1R 宏观框架)
# ==========================================


class LegalEntityType(Enum):
    LEGAL_ACTORS = "法律主体(WHO)"
    WHAT_CONCEPTS = "核心事项(WHAT)"
    WHEN_CONTEXTS = "时间情境(WHEN)"
    WHERE_CONTEXTS = "空间情境(WHERE)"
    RULE_TYPES = "规则类型(RULES)"
    BUSINESS_CATEGORIES = "业务分类"

# ==========================================
# 2. 实体抽取结果组件
# ==========================================


class NamedEntity(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    entity_name: str = Field(..., description="原文中提取的实体名称")
    entity_type: LegalEntityType = Field(..., description="所属的宏观实体类型")
    normalized_name: Optional[str] = Field(default=None, description="标准化实体名(映射至规范字典)")


class ClauseEntities(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    clause_id: str = Field(..., description="条文全局唯一ID")
    source_document: str = Field(..., description="来源法规")
    article_reference: str = Field(..., description="具体条目")
    content_original: str = Field(..., description="法条原文")
    extracted_entities: List[NamedEntity] = Field(default_factory=list)


def get_allowed_entity_types_str() -> str:
    return "、".join([e.value for e in LegalEntityType])

# ==========================================
# 3. 事件组装与逻辑裂变层 (Stage 3 输出)
# ==========================================


class RelationType(Enum):
    DEFAULT = "DEFAULT"
    EXCEPTION = "EXCEPTION"
    EXCLUSION = "EXCLUSION"


class ReviewReason(Enum):
    NONE = "NONE"
    AMBIGUOUS_TEXT = "AMBIGUOUS_TEXT"
    MISSING_PARAM = "MISSING_PARAM"


class FiveWOneH(BaseModel):
    who: str = Field(default="未指定", description="执行主体(对应WHO)")
    when: str = Field(default="未指定", description="触发时机/期间(对应WHEN)")
    where: str = Field(default="未指定", description="适用场景/系统(对应WHERE)")
    what: str = Field(default="未指定", description="核心对象(对应WHAT)")
    how: str = Field(default="未指定", description="具体动作/规则(对应RULES和文本原意)")

    # 💡 核心防御机制：Pydantic 拦截器。自动将大模型错误输出的 List 转为 String
    @field_validator("who", "when", "where", "what", "how", mode="before")
    @classmethod
    def convert_list_to_str(cls, v):
        if isinstance(v, list):
            # 遇到 ["单位", "个人"]，自动拼接为 "单位、个人"
            return "、".join([str(item) for item in v if item])
        if v is None:
            return "未指定"
        return str(v)


class LegalAtom(BaseModel):
    temp_id: str = Field(..., description="临时编号，如 '1', '2'")
    rule_type: str = Field(..., description="8大核心规则类型之一")
    behavior_struct: FiveWOneH = Field(..., description="5W1H深度结构化")
    relation_type: RelationType = Field(default=RelationType.DEFAULT)
    parent_atom_id: Optional[str] = Field(default=None)
    is_ambiguous: bool = Field(default=False)
    review_reason: ReviewReason = Field(default=ReviewReason.NONE)
    content_original: str = Field(..., description="该原子规则对应的原始切片文本")


class EventAssemblyResult(BaseModel):
    atoms: List[LegalAtom] = Field(..., description="拆分后的原子规则列表")

# ==========================================
# 4. 辅助函数 (ID生成器)
# ==========================================


def generate_atom_id(source_document: str, rule_type: str, index: int) -> str:
    """生成语义化ID: YZ-{法规代码}-{类型}-{序号}"""
    src_map = {
        "票据法": "NIL",
        "支付结算": "PSM",
        "反洗钱": "AML",
        "账户管理": "BAM",
        "商业银行": "CBL"
    }
    src_code = "GEN"
    for k, v in src_map.items():
        if k in source_document:
            src_code = v
            break

    try:
        type_code = rule_type.split("_")[0]
    except:
        type_code = "UNK"

    return f"YZ-{src_code}-{type_code}-{index:05d}"