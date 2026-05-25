import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from neo4j import GraphDatabase

from formal_qa import answer_question_formally
from neo4j_config import DEFAULT_NEO4J_URI, DEFAULT_NEO4J_USER, get_neo4j_password


LIVE_QUERY_CASES = [
    {
        "id": "Q01_broad_basic_account_material_stays_slow",
        "category": "材料",
        "question": "存款人开立基本存款账户时需要提交什么材料？",
        "query": "基本存款账户",
        "who": "存款人",
        "confidence": "llm-inferred",
        "fail_prefix": "unmatched_condition_scope:",
    },
    {
        "id": "Q02_scoped_offsite_basic_account_can_be_formal",
        "category": "材料",
        "question": "存款人异地开立基本存款账户应出具什么证明？",
        "query": "基本存款账户",
        "who": "存款人",
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q03_general_account_material_stays_slow",
        "category": "材料",
        "question": "存款人申请开立一般存款账户需要提交什么材料？",
        "query": "一般存款账户",
        "who": "存款人",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q04_overdue_bank_draft_material_stays_slow_when_scope_missing",
        "category": "材料",
        "question": "持票人持银行汇票提示付款需要提交什么材料？",
        "query": "银行汇票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "unmatched_condition_scope:",
    },
    {
        "id": "Q05_bank_draft_cash_without_cash_mark_stays_slow",
        "category": "许可",
        "question": "持票人持银行汇票提示付款能否支取现金？",
        "query": "银行汇票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "unmatched_condition_scope:",
    },
    {
        "id": "Q06_cash_marked_bank_note_can_be_formal",
        "category": "许可",
        "question": "持票人持注明现金字样的银行本票可以支取现金吗？",
        "query": "银行本票",
        "who": "持票人",
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q07_bank_note_overdue_scope_stays_slow_when_action_mismatches",
        "category": "流程",
        "question": "持票人超过银行本票提示付款期限后应如何处理？",
        "query": "银行本票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "unmatched_condition_scope:",
    },
    {
        "id": "Q08_suspicious_transaction_no_atoms",
        "category": "义务",
        "question": "银行在发现可疑交易时应当怎么做？",
        "query": "可疑交易",
        "who": "银行",
        "confidence": "llm-inferred",
        "fail_prefix": "no_atoms:",
    },
    {
        "id": "Q09_authority_query_answers_from_matching_action_only",
        "category": "主体",
        "question": "哪些机构有权核发开户登记证？",
        "query": "开户登记证",
        "who": None,
        "confidence": "formal",
        "answer_count": 1,
        "answer_actors": {"中国人民银行"},
    },
    {
        "id": "Q10_condition_query_stays_slow",
        "category": "条件",
        "question": "什么情况下需要重新核验客户身份？",
        "query": "客户身份",
        "who": "银行",
        "confidence": "llm-inferred",
        "fail_prefix": "unsupported_question_type:",
    },
    {
        "id": "Q11_forbidden_intent_mismatch_stays_slow",
        "category": "禁止",
        "question": "总包单位不得进行哪些操作？",
        "query": "农民工工资专用账户",
        "who": "总包单位",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q12_special_account_opening_can_be_formal",
        "category": "义务",
        "question": "总包单位开立农民工工资专用账户应当怎么做？",
        "query": "农民工工资专用账户",
        "who": "总包单位",
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q13_name_change_intent_mismatch_stays_slow",
        "category": "流程",
        "question": "单位银行结算账户的存款人变更名称应如何处理？",
        "query": "银行结算账户",
        "who": "存款人",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q14_payment_settlement_principle_insufficient_entities",
        "category": "原则",
        "question": "银行办理支付结算应遵守什么原则？",
        "query": "支付结算",
        "who": "银行",
        "confidence": "llm-inferred",
        "fail_prefix": "insufficient_entities:",
    },
    {
        "id": "Q15_check_required_items_too_many_atoms",
        "category": "记载",
        "question": "出票人签发支票需要记载哪些事项？",
        "query": "支票",
        "who": "出票人",
        "confidence": "llm-inferred",
        "fail_prefix": "too_many_atoms:",
    },
    {
        "id": "Q16_empty_check_forbidden_can_be_formal",
        "category": "禁止",
        "question": "支票的出票人不得签发什么支票？",
        "query": "支票",
        "who": "出票人",
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q17_bank_note_material_intent_mismatch_stays_slow",
        "category": "材料",
        "question": "持票人提示付款银行本票需要提交什么？",
        "query": "银行本票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q18_lost_instrument_remedy_can_be_formal",
        "category": "流程",
        "question": "票据丧失后失票人应如何处理？",
        "query": "票据丧失",
        "who": "失票人",
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q19_customer_identity_data_storage_can_be_formal",
        "category": "保存",
        "question": "金融机构保存客户身份资料有什么要求？",
        "query": "客户身份资料",
        "who": "金融机构",
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q20_customer_identity_identification_insufficient_entities",
        "category": "义务",
        "question": "金融机构识别客户身份时应当怎么做？",
        "query": "客户身份",
        "who": "金融机构",
        "confidence": "llm-inferred",
        "fail_prefix": "insufficient_entities:",
    },
    {
        "id": "Q21_bank_draft_multi_intent_stays_slow",
        "category": "多意图",
        "question": "未在银行开立存款账户的个人持票人，持银行汇票到银行提示付款，需要提交什么材料、如何签章、能否支取现金？",
        "query": "银行汇票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "complex_multi_intent:",
    },
    {
        "id": "Q22_commercial_bill_multi_intent_stays_slow",
        "category": "多意图",
        "question": "符合条件的商业汇票持票人向银行申请贴现，需要满足哪些条件、提供哪些材料、是否需要作成转让背书？",
        "query": "商业汇票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "complex_multi_intent:",
    },
    {
        "id": "Q23_bank_note_multi_intent_stays_slow",
        "category": "多意图",
        "question": "未在银行开立存款账户的个人持注明现金字样的银行本票向出票银行支取现金，是否可以委托他人提示付款；若超过提示付款期限未获付款或票据丧失，还应如何办理？",
        "query": "银行本票",
        "who": "持票人",
        "confidence": "llm-inferred",
        "fail_prefix": "complex_multi_intent:",
    },
    {
        "id": "Q24_definition_returns_only_definition_atoms",
        "category": "定义",
        "question": "临时存款账户是什么意思？",
        "query": "临时存款账户",
        "who": "存款人",
        "confidence": "formal",
        "answer_count": 1,
        "rule_types": {"DEF_SCOPE"},
    },
    {
        "id": "Q25_registration_certificate_management_subject_can_be_formal",
        "category": "主体",
        "question": "谁负责管理开户登记证？",
        "query": "开户登记证",
        "who": None,
        "confidence": "formal",
        "min_answers": 1,
    },
    {
        "id": "Q26_bank_note_issuer_qualification_subject_can_be_formal",
        "category": "主体",
        "question": "哪些机构负责审定本票出票人资格？",
        "query": "本票出票人资格",
        "who": None,
        "confidence": "formal",
        "answer_actors": {"中国人民银行"},
    },
    {
        "id": "Q27_bill_voucher_format_subject_stays_slow_when_object_extraction_is_too_broad",
        "category": "主体",
        "question": "谁有权制定票据凭证的格式和印制管理办法？",
        "query": "票据凭证的格式和印制管理办法",
        "who": None,
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q28_precise_registration_certificate_review_stays_slow",
        "category": "主体",
        "question": "哪些机构审核基本存款账户开户登记证？",
        "query": "基本存款账户开户登记证",
        "who": None,
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q29_wage_special_account_definition_can_be_formal",
        "category": "定义",
        "question": "农民工工资专用账户是什么意思？",
        "query": "农民工工资专用账户",
        "who": "总包单位",
        "confidence": "formal",
        "answer_count": 1,
        "rule_types": {"DEF_SCOPE"},
    },
    {
        "id": "Q30_bad_check_definition_can_be_formal",
        "category": "定义",
        "question": "空头支票是什么意思？",
        "query": "空头支票",
        "who": "出票人",
        "confidence": "formal",
        "answer_count": 1,
        "rule_types": {"DEF_SCOPE"},
        "answer_text_contains": "空头支票",
    },
    {
        "id": "Q31_definition_with_unmatched_only_receive_no_pay_stays_slow",
        "category": "定义",
        "question": "临时存款账户只收不付是什么意思？",
        "query": "临时存款账户",
        "who": "银行",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q32_condition_trigger_stays_slow",
        "category": "条件",
        "question": "什么情况下银行业金融机构应把临时存款账户调整为只收不付？",
        "query": "临时存款账户",
        "who": "银行业金融机构",
        "confidence": "llm-inferred",
        "fail_prefix": "unsupported_question_type:",
    },
    {
        "id": "Q33_only_receive_no_pay_permission_stays_slow",
        "category": "许可",
        "question": "临时存款账户存续期间是否只收不付？",
        "query": "临时存款账户",
        "who": "银行业金融机构",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q34_scoped_overdue_bank_draft_material_can_be_formal",
        "category": "材料",
        "question": "持票人超过银行汇票提示付款期限向代理付款银行提示付款不获付款后需要提交什么材料？",
        "query": "银行汇票",
        "who": "持票人",
        "confidence": "formal",
        "answer_count": 1,
    },
    {
        "id": "Q35_cash_marked_bank_draft_can_be_formal",
        "category": "许可",
        "question": "持票人持填明现金字样的银行汇票提示付款能否支取现金？",
        "query": "银行汇票",
        "who": "持票人",
        "confidence": "formal",
        "answer_count": 1,
    },
    {
        "id": "Q36_lost_bank_draft_permission_stays_slow_due_entity_alignment",
        "category": "许可",
        "question": "银行汇票丧失后失票人可以请求付款或退款吗？",
        "query": "银行汇票丧失",
        "who": "失票人",
        "confidence": "llm-inferred",
        "fail_prefix": "no_atoms:",
    },
    {
        "id": "Q37_bad_check_permission_stays_slow",
        "category": "许可",
        "question": "出票人签发空头支票可以吗？",
        "query": "空头支票",
        "who": "出票人",
        "confidence": "llm-inferred",
        "fail_prefix": "intent_mismatch:",
    },
    {
        "id": "Q38_third_party_identity_responsibility_stays_slow",
        "category": "义务",
        "question": "金融机构通过第三方代为履行客户身份识别义务时责任由谁承担？",
        "query": "客户身份识别义务",
        "who": "金融机构",
        "confidence": "llm-inferred",
        "fail_prefix": "insufficient_entities:",
    },
    {
        "id": "Q39_registration_certificate_issuing_process_can_be_formal",
        "category": "流程",
        "question": "开户银行受理基本存款账户开户申请后是否核发开户登记证？",
        "query": "基本存款账户开户申请",
        "who": "开户银行",
        "confidence": "formal",
        "answer_count": 1,
        "answer_text_contains": "核发开户登记证",
    },
    {
        "id": "Q40_transaction_record_storage_can_be_formal",
        "category": "保存",
        "question": "金融机构保存客户交易记录有什么要求？",
        "query": "交易记录",
        "who": "金融机构",
        "confidence": "formal",
        "min_answers": 1,
    },
]


class FormalQALiveQuerySmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        password = get_neo4j_password()
        if not password:
            raise unittest.SkipTest("NEO4J_PASSWORD is not configured.")
        cls.driver = GraphDatabase.driver(
            DEFAULT_NEO4J_URI,
            auth=(DEFAULT_NEO4J_USER, password),
        )
        try:
            cls.driver.verify_connectivity()
        except Exception as exc:
            cls.driver.close()
            raise unittest.SkipTest(f"Neo4j is not available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        driver = getattr(cls, "driver", None)
        if driver is not None:
            driver.close()

    def test_live_query_tightness_cases(self):
        for case in LIVE_QUERY_CASES:
            with self.subTest(case=case["id"]):
                result = answer_question_formally(
                    question=case["question"],
                    graph=self.driver,
                    query=case["query"],
                    who=case["who"],
                )
                self.assertEqual(result["confidence"], case["confidence"])

                if "fail_prefix" in case:
                    self.assertTrue(
                        str(result.get("fail_reason", "")).startswith(case["fail_prefix"]),
                        result.get("fail_reason"),
                    )
                if "min_answers" in case:
                    self.assertGreaterEqual(result.get("answer_count", 0), case["min_answers"])
                if "answer_count" in case:
                    self.assertEqual(result.get("answer_count", 0), case["answer_count"])
                if "rule_types" in case:
                    self.assertEqual(
                        {atom.get("rule_type") for atom in result.get("atoms", [])},
                        case["rule_types"],
                    )
                if "answer_actors" in case:
                    self.assertEqual(
                        {answer.get("answer_actor") for answer in result.get("answers", [])},
                        case["answer_actors"],
                    )
                if "answer_text_contains" in case:
                    self.assertTrue(
                        any(case["answer_text_contains"] in answer.get("text", "") for answer in result.get("answers", [])),
                        result.get("answers", []),
                    )


if __name__ == "__main__":
    unittest.main()
