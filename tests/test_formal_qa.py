import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import formal_qa


def no_conflicts(*_args, **_kwargs):
    return {"summary": {"group_count": 0}}


def make_atom(**overrides):
    atom = {
        "atom_id": "A-001",
        "rule_type": "OBL_MANDATORY",
        "source_document": "人民币银行结算账户管理办法",
        "article_reference": "第二十三条第一款",
        "who": "存款人",
        "what": "基本存款账户",
        "when": "经营地与注册地不在同一行政区域时",
        "how": "应出具注册地中国人民银行分支行的未开立基本存款账户的证明",
        "content_original": "经营地与注册地不在同一行政区域的存款人，在异地开立基本存款账户的，应出具证明。",
        "is_ambiguous": False,
        "actor_match": True,
        "object_match": True,
        "time_match": False,
        "has_scene_match": False,
        "match_count": 2,
        "actor_values": ["存款人"],
        "object_values": ["基本存款账户"],
        "time_values": ["经营地与注册地不在同一行政区域", "异地开立基本存款账户"],
    }
    atom.update(overrides)
    return atom


class FormalQATest(unittest.TestCase):
    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch("formal_qa.retrieve_type_a_atoms")
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_a_rejects_unmatched_condition_scope(self, extract_terms, retrieve_atoms, _conflicts):
        extract_terms.return_value = {
            "actors": ["存款人"],
            "objects": ["基本存款账户"],
            "times": [],
        }
        retrieve_atoms.return_value = [make_atom()]

        result = formal_qa.answer_question_formally(
            question="存款人开立基本存款账户时需要提交什么材料？",
            graph=object(),
            query="基本存款账户",
            who="存款人",
        )

        self.assertEqual(result["confidence"], "llm-inferred")
        self.assertTrue(result["fail_reason"].startswith("unmatched_condition_scope:"))
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["A-001"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_a_atoms",
        return_value=[
            make_atom(
                atom_id="A-001",
                rule_type="PER_AUTH",
                who="出票人",
                what="支票金额",
                when="签发支票时",
                how="授权他人补记",
                content_original="支票上的金额可以由出票人授权补记。",
                actor_values=["出票人"],
                object_values=["支票"],
                time_values=[],
            ),
            make_atom(
                atom_id="A-002",
                rule_type="PRO_FORBIDDEN",
                who="出票人",
                what="空头支票",
                when="签发支票时",
                how="禁止签发",
                content_original="禁止签发空头支票。",
                actor_values=["出票人"],
                object_values=["支票"],
                time_values=[],
            ),
        ],
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_a_rejects_broad_permission_when_precise_object_mismatches(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": ["出票人"],
            "objects": ["支票"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="出票人签发空头支票可以吗？",
            graph=object(),
            query="空头支票",
            who="出票人",
        )

        self.assertEqual(result["confidence"], "llm-inferred")
        self.assertTrue(result["fail_reason"].startswith("intent_mismatch:"))
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["A-002"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_a_atoms",
        return_value=[
            make_atom(
                atom_id="A-003",
                rule_type="PRC_FLOW",
                who="银行业金融机构",
                what="临时存款账户内资金",
                when="办理临时存款账户销户时",
                how="必须转入同户名基本存款账户",
                content_original="临时存款账户资金必须转入同户名基本存款账户。",
                actor_values=["银行业金融机构"],
                object_values=["临时存款账户"],
                time_values=[],
            )
        ],
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_a_requires_all_precise_object_terms_to_match(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": ["银行业金融机构"],
            "objects": ["临时存款账户"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="临时存款账户存续期间是否只收不付？",
            graph=object(),
            query="临时存款账户",
            who="银行业金融机构",
        )

        self.assertEqual(result["confidence"], "llm-inferred")
        self.assertTrue(result["fail_reason"].startswith("intent_mismatch:"))
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["A-003"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_b_actor_answers",
        return_value=[
            make_atom(
                atom_id="B-001",
                rule_type="PER_AUTH",
                answer_actor="中国人民银行分支机构",
                how="核发开户登记证",
                when="核准开立基本存款账户时",
                content_original="中国人民银行分支机构对核准开立的账户核发开户登记证。",
                time_values=["核准开立基本存款账户时"],
            )
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_b_actor_query_can_return_formal_actor_answer(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": [],
            "objects": ["开户登记证"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="哪些机构有权核发开户登记证？",
            graph=object(),
            query="开户登记证",
        )

        self.assertEqual(result["confidence"], "formal")
        self.assertEqual(result["question_type"], "B")
        self.assertEqual(result["answers"][0]["answer_actor"], "中国人民银行分支机构")
        self.assertIn("中国人民银行分支机构", result["answers"][0]["text"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_b_actor_answers",
        return_value=[
            make_atom(
                atom_id="B-001",
                rule_type="PRC_FLOW",
                answer_actor="开户银行",
                how="经中国人民银行核准后由开户银行核发开户登记证",
                when="受理开户申请后",
                content_original="经中国人民银行核准后由开户银行核发开户登记证。",
                time_values=["受理开户申请后"],
            ),
            make_atom(
                atom_id="B-002",
                rule_type="OBL_MANDATORY",
                answer_actor="银行",
                how="审核并留存基本存款账户开户登记证",
                when="开立临时存款账户时",
                content_original="银行还应审核并留存其基本存款账户开户登记证。",
                time_values=["开立账户时"],
            ),
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_b_actor_query_filters_to_matching_action(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": [],
            "objects": ["开户登记证"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="哪些机构有权核发开户登记证？",
            graph=object(),
            query="开户登记证",
        )

        self.assertEqual(result["confidence"], "formal")
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["B-001"])
        self.assertEqual([answer["answer_actor"] for answer in result["answers"]], ["开户银行"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_b_actor_answers",
        return_value=[
            make_atom(
                atom_id="B-002",
                rule_type="OBL_MANDATORY",
                answer_actor="银行",
                how="审核并留存基本存款账户开户登记证",
                when="开立临时存款账户时",
                content_original="银行还应审核并留存其基本存款账户开户登记证。",
                time_values=["开立账户时"],
            )
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_b_actor_query_rejects_unmatched_action(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": [],
            "objects": ["开户登记证"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="哪些机构有权核发开户登记证？",
            graph=object(),
            query="开户登记证",
        )

        self.assertEqual(result["confidence"], "llm-inferred")
        self.assertTrue(result["fail_reason"].startswith("intent_mismatch:"))
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["B-002"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_b_actor_answers",
        return_value=[
            make_atom(
                atom_id="B-002",
                rule_type="OBL_MANDATORY",
                answer_actor="试点地区银行",
                what="企业基本存款账户信息",
                how="经审核符合变更条件后办理信息变更手续",
                content_original="经审核符合变更条件的，试点地区银行为企业办理基本存款账户信息变更手续。",
                object_values=["基本存款账户"],
                time_values=[],
            )
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_b_actor_query_rejects_precise_object_mismatch(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": [],
            "objects": ["基本存款账户", "开户登记证"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="哪些机构审核基本存款账户开户登记证？",
            graph=object(),
            query="基本存款账户开户登记证",
        )

        self.assertEqual(result["confidence"], "llm-inferred")
        self.assertTrue(result["fail_reason"].startswith("intent_mismatch:"))
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["B-002"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_c_definition_answers",
        return_value=[
            make_atom(
                atom_id="C-001",
                rule_type="DEF_SCOPE",
                what="临时存款账户",
                when="",
                time_values=[],
                how="临时存款账户是存款人因临时需要并在规定期限内使用而开立的银行结算账户",
                content_original="临时存款账户是存款人因临时需要并在规定期限内使用而开立的银行结算账户。",
            )
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_c_definition_query_can_return_formal_definition_answer(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": ["存款人"],
            "objects": ["临时存款账户"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="临时存款账户是什么意思？",
            graph=object(),
            query="临时存款账户",
            who="存款人",
        )

        self.assertEqual(result["confidence"], "formal")
        self.assertEqual(result["question_type"], "C")
        self.assertIn("临时存款账户", result["answers"][0]["text"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_c_definition_answers",
        return_value=[
            make_atom(
                atom_id="C-001",
                rule_type="DEF_SCOPE",
                what="临时存款账户",
                when="",
                time_values=[],
                how="临时存款账户是存款人因临时需要并在规定期限内使用而开立的银行结算账户",
                content_original="临时存款账户是存款人因临时需要并在规定期限内使用而开立的银行结算账户。",
            ),
            make_atom(
                atom_id="C-002",
                rule_type="VAL_THRESHOLD",
                what="临时存款账户资金收付权限",
                when="临时存款账户存续期间",
                time_values=[],
                how="设置为只收不付",
                content_original="临时存款账户只收不付。",
            ),
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_c_definition_query_filters_non_definition_atoms(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": ["存款人"],
            "objects": ["临时存款账户"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="临时存款账户是什么意思？",
            graph=object(),
            query="临时存款账户",
            who="存款人",
        )

        self.assertEqual(result["confidence"], "formal")
        self.assertEqual([atom["atom_id"] for atom in result["atoms"]], ["C-001"])
        self.assertEqual(result["answer_count"], 1)
        self.assertNotIn("只收不付", result["answers"][0]["text"])

    @patch("formal_qa.detect_atom_conflicts", side_effect=no_conflicts)
    @patch(
        "formal_qa.retrieve_type_c_definition_answers",
        return_value=[
            make_atom(
                atom_id="C-003",
                rule_type="DEF_SCOPE",
                what="空头支票",
                answer_object="支票",
                when="",
                time_values=[],
                how="出票人签发的支票金额超过其付款时在付款人处实有的存款金额",
                content_original="空头支票是出票人签发的支票金额超过其付款时在付款人处实有的存款金额。",
            )
        ],
        create=True,
    )
    @patch("formal_qa.extract_formal_query_terms")
    def test_type_c_definition_answer_prefers_atom_what_over_normalized_object(self, extract_terms, _retrieve, _conflicts):
        extract_terms.return_value = {
            "actors": ["出票人"],
            "objects": ["支票"],
            "times": [],
        }

        result = formal_qa.answer_question_formally(
            question="空头支票是什么意思？",
            graph=object(),
            query="空头支票",
            who="出票人",
        )

        self.assertEqual(result["confidence"], "formal")
        self.assertIn("空头支票的定义", result["answers"][0]["text"])


if __name__ == "__main__":
    unittest.main()
