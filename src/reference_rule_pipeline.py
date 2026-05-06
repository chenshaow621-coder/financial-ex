import argparse
import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from docx import Document

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
REF_XLSX = next(RAW.glob("*.xlsx"))
FULL_ATOMS = PROC / "legal_atoms_v4_final.xlsx"

DOC_BILL = "中华人民共和国票据法"
DOC_PAY = "支付结算办法"
APPENDIX_TITLE = "正确填写票据和结算凭证的基本规定"
APPENDIX_2 = "附二"

CHECK_SPECS = {
    1: {"check_type": "ISSUE_DATE_WITHIN_10_DAYS", "required_fields": ["issue_date", "core_date"], "params": {"max_days": 10}},
    2: {"check_type": "ISSUE_DATE_IN_CHINESE_UPPERCASE", "required_fields": ["issue_date", "issue_date_chinese"], "params": {}},
    3: {"check_type": "LOWER_AMOUNT_HAS_RMB_SYMBOL", "required_fields": ["amount_numeric"], "params": {"required_symbol": "￥"}},
    4: {"check_type": "UPPER_AMOUNT_EQUALS_LOWER_AMOUNT", "required_fields": ["amount_numeric", "amount_upper"], "params": {}},
    5: {"check_type": "AMOUNT_IS_REQUIRED", "required_fields": ["amount_numeric"], "params": {}},
    6: {"check_type": "UPPER_AMOUNT_FORMAT_IS_VALID", "required_fields": ["amount_upper"], "params": {}},
    7: {"check_type": "SIGNATURE_SET_IS_COMPLETE", "required_fields": ["signatures"], "params": {"finance": ["财务专用章", "公章"], "personal": ["个人章", "法人章", "法定代表人章", "签名", "签字", "代理人签章"]}},
}
ENTITY_TERMS = [
    "转账支票", "支票", "票据", "出票日期", "核心日期", "提示付款", "小写金额", "大写金额", "金额", "签章", "财务专用章", "个人章", "出票人", "持票人", "付款人", "中国人民银行", "人民币符号"
]
MANUAL_REFS = {
    1: {DOC_BILL: ["第九十二条"]},
    2: {DOC_BILL: ["第八十五条"], DOC_PAY: ["附一-六"]},
    3: {DOC_PAY: ["附一-五"]},
    4: {DOC_BILL: ["第八条"], DOC_PAY: ["第十三条"]},
    5: {DOC_BILL: ["第八十五条"], DOC_PAY: ["第一百一十八条"]},
    6: {DOC_PAY: ["附一-一", "附一-二", "附一-三", "附一-四"]},
    7: {DOC_BILL: ["第四条", "第七条", "第八十五条"]},
}

CN_DIGITS = {"零": 0, "〇": 0, "壹": 1, "贰": 2, "貳": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "陸": 6, "柒": 7, "捌": 8, "玖": 9}
CN_UNITS = {"拾": 10, "佰": 100, "仟": 1000}
CN_SECTION = {"万": 10000, "萬": 10000, "亿": 100000000, "億": 100000000}


def norm(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("O", "0").replace("o", "0")
    text = text.replace("入民币", "人民币")
    text = text.replace("出禀日期", "出票日期")
    text = re.sub(r"\s+", "", text)
    for ch in "，,。；;：:“”\"'‘’（）()【】[]—-":
        text = text.replace(ch, "")
    return text


def paragraphs(path):
    doc = Document(str(path))
    return [p.text.replace(" ", " ").strip() for p in doc.paragraphs if p.text.strip()]


def parse_article_segments(doc_name, lines):
    segs, article_ref, buf = [], None, []

    def flush():
        nonlocal article_ref, buf
        if not article_ref or not buf:
            return
        segs.append({"id": f"{doc_name}:{article_ref}:BLOCK", "doc": doc_name, "ref": article_ref, "type": "article_block", "text": "\n".join(buf)})
        for idx, line in enumerate(buf, 1):
            segs.append({"id": f"{doc_name}:{article_ref}:P{idx}", "doc": doc_name, "ref": f"{article_ref}#P{idx}", "type": "article_paragraph", "text": line})
        article_ref, buf = None, []

    for line in lines:
        match = re.match(r"^(第[一二三四五六七八九十百零〇0-9]+条)", line)
        if match:
            flush()
            article_ref = match.group(1)
            buf = [line]
            continue
        if re.match(r"^第[一二三四五六七八九十百零〇0-9]+[章节]", line):
            flush()
            continue
        if article_ref:
            buf.append(line)
    flush()
    return segs


def parse_payment_segments(lines):
    start = next((i for i, line in enumerate(lines) if line.startswith(APPENDIX_TITLE)), len(lines))
    segs = parse_article_segments(DOC_PAY, lines[:start])
    item_no, buf = None, []

    def flush_item():
        nonlocal item_no, buf
        if item_no and buf:
            segs.append({"id": f"{DOC_PAY}:附一-{item_no}:BLOCK", "doc": DOC_PAY, "ref": f"附一-{item_no}", "type": "appendix_item", "text": "\n".join(buf)})
        item_no, buf = None, []

    for line in lines[start:]:
        if line.startswith(APPENDIX_2):
            break
        if not line or line.startswith(APPENDIX_TITLE):
            continue
        match = re.match(r"^([一二三四五六七八九十]+)、", line)
        if match:
            flush_item()
            item_no = match.group(1)
            buf = [line]
            continue
        if item_no:
            buf.append(line)
            sub = re.match(r"^(（[一二三四五六七八九十]+）)", line)
            if sub:
                segs.append({"id": f"{DOC_PAY}:附一-{item_no}{sub.group(1)}", "doc": DOC_PAY, "ref": f"附一-{item_no}{sub.group(1)}", "type": "appendix_subitem", "text": line})
    flush_item()
    return segs


def load_segments():
    files = {}
    for path in RAW.glob("*.docx"):
        if "票据法" in path.name:
            files[DOC_BILL] = path
        if "支付结算办法" in path.name:
            files[DOC_PAY] = path
    return {
        DOC_BILL: parse_article_segments(DOC_BILL, paragraphs(files[DOC_BILL])),
        DOC_PAY: parse_payment_segments(paragraphs(files[DOC_PAY])),
    }


def split_reference_source(doc_name, text):
    text = str(text or "").strip()
    if not text:
        return []
    if doc_name == DOC_PAY and "附一" in text:
        text = re.sub(r"^附一[:：]?\s*", "", text)
        parts = re.split(r"(?=[一二三四五六七八九十]+、)", text)
    else:
        parts = re.split(r"(?=第[一二三四五六七八九十百零〇0-9]+条)", text)
    return [part.strip() for part in parts if part and part.strip()]


def chunk_hint(chunk):
    match = re.match(r"^(第[一二三四五六七八九十百零〇0-9]+条)", chunk)
    if match:
        return match.group(1)
    match = re.match(r"^([一二三四五六七八九十]+)、", chunk)
    return match.group(1) if match else None


def overlap_score(left, right):
    left, right = norm(left), norm(right)
    if not left or not right:
        return 0.0
    common = sum(1 for ch in left if ch in set(right))
    return min(1.0, common / len(left) * 0.8 + (0.2 if left in right or right in left else 0.0))


def match_chunk(chunk, segments):
    hint = chunk_hint(chunk)
    candidates = list(segments)
    if hint:
        if hint.startswith("第"):
            tmp = [seg for seg in candidates if seg["ref"].startswith(hint)]
        else:
            tmp = [seg for seg in candidates if seg["ref"].startswith(f"附一-{hint}")]
        if tmp:
            candidates = tmp
    best = max(candidates, key=lambda seg: overlap_score(chunk, seg["text"]))
    return best, round(overlap_score(chunk, best["text"]), 4)


def load_atoms():
    if not FULL_ATOMS.exists():
        return pd.DataFrame()
    df = pd.read_excel(FULL_ATOMS).fillna("")
    df = df[df["source_document"].isin([DOC_BILL, DOC_PAY])].copy()
    df["content_norm"] = df["content_original"].map(norm)
    return df


def align_atoms(segment, atoms):
    if atoms.empty:
        return []
    subset = atoms[atoms["source_document"] == segment["doc"]].copy()
    target = norm(segment["text"])
    hit = subset[subset["content_norm"].map(lambda value: bool(value) and (value in target or target in value))].copy()
    if hit.empty:
        subset["score"] = subset["content_original"].map(lambda value: overlap_score(segment["text"], value))
        hit = subset[subset["score"] >= 0.45].copy()
    else:
        hit["score"] = hit["content_original"].map(lambda value: overlap_score(segment["text"], value))
    if hit.empty:
        return []
    return hit.sort_values(["score", "atom_id"], ascending=[False, True])["atom_id"].astype(str).tolist()


def infer_rule_type(text):
    if "不得" in text or "不予受理" in text:
        return "PRO_FORBIDDEN"
    if "可以" in text and "不得" not in text:
        return "PER_AUTH"
    if re.search(r"\d+日内|\d+个月|一致|期限|比较", text):
        return "VAL_THRESHOLD"
    if "是指" in text:
        return "DEF_SCOPE"
    return "OBL_MANDATORY" if any(word in text for word in ["应当", "必须", "须"]) else "EVT_TRIGGER"


def extract_entities(*texts):
    merged = "\n".join(texts)
    return json.dumps([term for term in ENTITY_TERMS if term in merged], ensure_ascii=False)


def build_catalog():
    segments_by_doc = load_segments()
    atoms = load_atoms()
    reference = pd.read_excel(REF_XLSX, sheet_name="Sheet1").fillna("").iloc[1:]
    rules, aligns, fallbacks, linked = [], [], [], {}
    fallback_idx = 1
    for _, row in reference.iterrows():
        seq = int(row["序号"])
        rule_id = f"REF-{seq:03d}"
        expert_rule = str(row["同业原专家经验规则"]).strip()
        spec = CHECK_SPECS[seq]
        atom_ids = []
        entity_terms = [expert_rule]
        for col_idx, doc_name in [(2, DOC_BILL), (3, DOC_PAY)]:
            source_text = str(row.iloc[col_idx]).strip()
            manual_refs = MANUAL_REFS.get(seq, {}).get(doc_name, [])
            manual_segs = [seg for seg in segments_by_doc[doc_name] if seg["ref"] in manual_refs and seg["type"] in {"article_block", "appendix_item"}]
            chunk_pairs = [(seg["text"], seg, 1.0) for seg in manual_segs] if manual_segs else []
            if not chunk_pairs:
                for chunk in split_reference_source(doc_name, source_text):
                    seg, score = match_chunk(chunk, segments_by_doc[doc_name])
                    chunk_pairs.append((chunk, seg, score))
            for chunk, seg, score in chunk_pairs:
                ids = align_atoms(seg, atoms)
                if not ids:
                    fallback_id = f"REF-ATOM-{fallback_idx:03d}"
                    fallback_idx += 1
                    fallbacks.append({
                        "atom_id": fallback_id,
                        "rule_type": infer_rule_type(seg["text"]),
                        "source_document": seg["doc"],
                        "article_reference": seg["ref"],
                        "who": "",
                        "what": "",
                        "how": seg["text"],
                        "content_original": seg["text"],
                        "linked_rule_ids": rule_id,
                    })
                    ids = [fallback_id]
                atom_ids.extend(ids)
                entity_terms.extend([chunk, seg["text"]])
                aligns.append({
                    "rule_id": rule_id,
                    "sequence": seq,
                    "check_type": spec["check_type"],
                    "document_name": doc_name,
                    "source_chunk": chunk,
                    "matched_segment_id": seg["id"],
                    "matched_segment_ref": seg["ref"],
                    "matched_segment_type": seg["type"],
                    "matched_segment_text": seg["text"],
                    "match_score": score,
                    "aligned_existing_atom_ids": json.dumps(ids, ensure_ascii=False),
                })
        linked[rule_id] = sorted(set(atom_ids))
        rules.append({
            "rule_id": rule_id,
            "sequence": seq,
            "expert_rule": expert_rule,
            "check_type": spec["check_type"],
            "required_fields": json.dumps(spec["required_fields"], ensure_ascii=False),
            "params": json.dumps(spec["params"], ensure_ascii=False),
            "entities": extract_entities(*entity_terms),
            "linked_atom_ids": json.dumps(linked[rule_id], ensure_ascii=False),
        })
    rule_df = pd.DataFrame(rules).sort_values("sequence")
    align_df = pd.DataFrame(aligns).sort_values(["sequence", "document_name", "matched_segment_ref"])
    frames = []
    if not atoms.empty:
        wanted = {atom_id for ids in linked.values() for atom_id in ids if not str(atom_id).startswith("REF-ATOM-")}
        if wanted:
            picked = atoms[atoms["atom_id"].astype(str).isin(wanted)].copy()
            rev = {}
            for rule_id, ids in linked.items():
                for atom_id in ids:
                    rev.setdefault(atom_id, []).append(rule_id)
            picked["linked_rule_ids"] = picked["atom_id"].astype(str).map(lambda atom_id: json.dumps(sorted(rev.get(atom_id, [])), ensure_ascii=False))
            frames.append(picked)
    if fallbacks:
        frames.append(pd.DataFrame(fallbacks))
    atom_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["atom_id", "rule_type", "source_document", "article_reference", "content_original", "linked_rule_ids"])
    kg = build_kg(rule_df, align_df, atom_df)
    return rule_df, align_df, atom_df, kg


def build_kg(rule_df, align_df, atom_df):
    nodes, edges, seen = [], [], set()
    def add(node_type, node_id, **props):
        if (node_type, node_id) in seen:
            return
        seen.add((node_type, node_id))
        nodes.append({"type": node_type, "id": node_id, **props})
    for _, row in rule_df.iterrows():
        add("ReferenceRule", row["rule_id"], name=row["expert_rule"], check_type=row["check_type"], entities=row["entities"])
    for _, row in align_df.iterrows():
        doc_id = f"DOC:{row['document_name']}"
        add("Document", doc_id, name=row["document_name"])
        add("ClauseSegment", row["matched_segment_id"], ref=row["matched_segment_ref"], segment_type=row["matched_segment_type"], text=row["matched_segment_text"])
        edges.append({"source": row["rule_id"], "target": row["matched_segment_id"], "type": "SUPPORTED_BY"})
        edges.append({"source": row["matched_segment_id"], "target": doc_id, "type": "FROM_DOCUMENT"})
        for atom_id in json.loads(row["aligned_existing_atom_ids"]):
            edges.append({"source": row["rule_id"], "target": atom_id, "type": "LINKS_TO_ATOM"})
            edges.append({"source": row["matched_segment_id"], "target": atom_id, "type": "YIELDS_ATOM"})
    for _, row in atom_df.iterrows():
        add("LegalAtom", str(row["atom_id"]), source_document=row.get("source_document", ""), article_reference=row.get("article_reference", ""), rule_type=row.get("rule_type", ""), content_original=row.get("content_original", ""))
    return {"nodes": nodes, "edges": edges}


def save_build(rule_df, align_df, atom_df, kg):
    PROC.mkdir(parents=True, exist_ok=True)
    paths = {
        "rule_catalog": PROC / "reference_rule_catalog.xlsx",
        "alignment": PROC / "reference_rule_alignment.xlsx",
        "atom_catalog": PROC / "reference_rule_atoms.xlsx",
        "kg_json": PROC / "reference_rule_kg.json",
    }
    rule_df.to_excel(paths["rule_catalog"], index=False)
    align_df.to_excel(paths["alignment"], index=False)
    atom_df.to_excel(paths["atom_catalog"], index=False)
    paths["kg_json"].write_text(json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {name: str(path) for name, path in paths.items()}


def parse_numeric_amount(value):
    if value is None or str(value).strip() == "":
        return None
    text = unicodedata.normalize("NFKC", str(value)).replace("￥", "").replace("¥", "").replace(",", "").replace("，", "").strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_date_value(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = unicodedata.normalize("NFKC", str(value)).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def parse_cn_integer(text):
    total = 0
    section = 0
    number = 0
    for ch in text:
        if ch in CN_DIGITS:
            number = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            if number == 0:
                number = 1
            section += number * CN_UNITS[ch]
            number = 0
        elif ch in CN_SECTION:
            section += number
            total += section * CN_SECTION[ch]
            section = 0
            number = 0
    return total + section + number


def parse_upper_amount(value):
    if value is None or str(value).strip() == "":
        return None
    text = unicodedata.normalize("NFKC", str(value)).replace("人民币", "").replace("圆", "元").replace("圓", "元").replace("整", "").replace("正", "").strip()
    if not text:
        return None
    if "元" in text:
        integer_part, decimal_part = text.split("元", 1)
    else:
        integer_part, decimal_part = text, ""
    value_decimal = Decimal(parse_cn_integer(integer_part) if integer_part else 0)
    match = re.search(r"([零壹贰貳叁肆伍陆陸柒捌玖])角", decimal_part)
    if match:
        value_decimal += Decimal(CN_DIGITS[match.group(1)]) / Decimal("10")
    match = re.search(r"([零壹贰貳叁肆伍陆陸柒捌玖])分", decimal_part)
    if match:
        value_decimal += Decimal(CN_DIGITS[match.group(1)]) / Decimal("100")
    return value_decimal


def month_forms(month):
    digit = {1: "壹", 2: "贰", 3: "叁", 4: "肆", 5: "伍", 6: "陆", 7: "柒", 8: "捌", 9: "玖"}
    if month in (1, 2):
        return [f"零{digit[month]}月"]
    if 3 <= month <= 9:
        return [f"零{digit[month]}月", f"{digit[month]}月"]
    if month == 10:
        return ["壹拾月", "零壹拾月"]
    if month == 11:
        return ["壹拾壹月", "零壹拾壹月"]
    return ["壹拾贰月", "零壹拾贰月"]


def day_forms(day):
    digit = {1: "壹", 2: "贰", 3: "叁", 4: "肆", 5: "伍", 6: "陆", 7: "柒", 8: "捌", 9: "玖"}
    if 1 <= day <= 9:
        return [f"零{digit[day]}日"]
    if day == 10:
        return ["壹拾日", "零壹拾日"]
    if 11 <= day <= 19:
        return [f"壹拾{digit[day - 10]}日", f"零壹拾{digit[day - 10]}日"]
    if day == 20:
        return ["贰拾日"]
    if 21 <= day <= 29:
        return [f"贰拾{digit[day - 20]}日"]
    if day == 30:
        return ["叁拾日"]
    return ["叁拾壹日"]


def valid_issue_date_forms(dt):
    return [month_text + day_text for month_text in month_forms(dt.month) for day_text in day_forms(dt.day)]


def validate_upper_format(amount_upper, amount_numeric):
    if not amount_upper:
        return False, "缺少大写金额。"
    normalized = unicodedata.normalize("NFKC", str(amount_upper)).replace("人民币", "")
    pattern = r"^[人民币零〇壹贰貳叁肆伍陆陸柒捌玖拾佰仟万萬亿億圆元角分整正\s]+$"
    if not re.match(pattern, str(amount_upper)):
        return False, "包含非法字符。"
    has_decimal = "角" in normalized or "分" in normalized
    end_complete = normalized.strip().endswith(("整", "正"))
    if not has_decimal and not end_complete:
        return False, "金额到元为止时，元后应写整/正。"
    if "分" in normalized and end_complete:
        return False, "有分时，后面不应再写整/正。"
    parsed = parse_upper_amount(amount_upper)
    if parsed is None:
        return False, "无法解析大写金额。"
    if amount_numeric is not None and parsed != amount_numeric:
        return False, f"大写解析值 {parsed} 与小写金额 {amount_numeric} 不一致。"
    return True, "大写金额格式通过。"


def validate_rule(sequence, payload):
    spec = CHECK_SPECS[sequence]
    check_type = spec["check_type"]
    if check_type == "ISSUE_DATE_WITHIN_10_DAYS":
        issue_date = parse_date_value(payload.get("issue_date"))
        core_date = parse_date_value(payload.get("core_date"))
        if not issue_date or not core_date:
            return {"status": "SKIP", "reason": "缺少 issue_date 或 core_date。"}
        delta = abs((core_date - issue_date).days)
        ok = delta <= spec["params"]["max_days"]
        return {"status": "PASS" if ok else "FAIL", "reason": f"日期差 {delta} 天。"}
    if check_type == "ISSUE_DATE_IN_CHINESE_UPPERCASE":
        issue_date = parse_date_value(payload.get("issue_date"))
        issue_date_text = str(payload.get("issue_date_chinese", "")).strip()
        if not issue_date or not issue_date_text:
            return {"status": "SKIP", "reason": "缺少 issue_date 或 issue_date_chinese。"}
        normalized = norm(issue_date_text)
        forms = valid_issue_date_forms(issue_date)
        ok = any(norm(form) in normalized or normalized.endswith(norm(form)) for form in forms)
        return {"status": "PASS" if ok else "FAIL", "reason": "出票日期中文大写通过。" if ok else f"不符合规则，期望形式之一：{forms}。"}
    if check_type == "LOWER_AMOUNT_HAS_RMB_SYMBOL":
        amount_text = str(payload.get("amount_numeric", "")).strip()
        if not amount_text:
            return {"status": "SKIP", "reason": "缺少 amount_numeric。"}
        ok = unicodedata.normalize("NFKC", amount_text).startswith("¥") or amount_text.startswith("￥")
        return {"status": "PASS" if ok else "FAIL", "reason": "小写金额包含人民币符号。" if ok else "缺少人民币符号￥。"}
    if check_type == "UPPER_AMOUNT_EQUALS_LOWER_AMOUNT":
        amount_numeric = parse_numeric_amount(payload.get("amount_numeric"))
        amount_upper = payload.get("amount_upper")
        if amount_numeric is None or not str(amount_upper).strip():
            return {"status": "SKIP", "reason": "缺少 amount_numeric 或 amount_upper。"}
        parsed_upper = parse_upper_amount(amount_upper)
        ok = parsed_upper == amount_numeric
        return {"status": "PASS" if ok else "FAIL", "reason": "大小写金额一致。" if ok else f"小写={amount_numeric}，大写解析={parsed_upper}。"}
    if check_type == "AMOUNT_IS_REQUIRED":
        ok = payload.get("amount_numeric") is not None and str(payload.get("amount_numeric")).strip() != ""
        return {"status": "PASS" if ok else "FAIL", "reason": "金额字段已填写。" if ok else "金额为空。"}
    if check_type == "UPPER_AMOUNT_FORMAT_IS_VALID":
        amount_upper = str(payload.get("amount_upper", "")).strip()
        amount_numeric = parse_numeric_amount(payload.get("amount_numeric"))
        if not amount_upper:
            return {"status": "SKIP", "reason": "缺少 amount_upper。"}
        ok, reason = validate_upper_format(amount_upper, amount_numeric)
        return {"status": "PASS" if ok else "FAIL", "reason": reason}
    signatures = payload.get("signatures")
    if signatures is None or signatures == "":
        return {"status": "SKIP", "reason": "缺少 signatures。"}
    if isinstance(signatures, str):
        values = [part.strip() for part in re.split(r"[，,；;|/]", signatures) if part.strip()]
    else:
        values = [str(item).strip() for item in signatures if str(item).strip()]
    has_finance = any(any(token in value for token in spec["params"]["finance"]) for value in values)
    has_personal = any(any(token in value for token in spec["params"]["personal"]) for value in values)
    if has_finance and has_personal:
        return {"status": "PASS", "reason": "签章组合满足要求。"}
    if not has_finance and not has_personal:
        return {"status": "FAIL", "reason": "同时缺少单位章与个人/法人签章。"}
    return {"status": "FAIL", "reason": "缺少财务专用章/公章。" if not has_finance else "缺少个人章/法人章/授权签章。"}


def run_check(payload, rule_df):
    results = []
    summary = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for _, row in rule_df.sort_values("sequence").iterrows():
        result = validate_rule(int(row["sequence"]), payload)
        result.update({"rule_id": row["rule_id"], "sequence": int(row["sequence"]), "expert_rule": row["expert_rule"], "check_type": row["check_type"]})
        summary[result["status"]] += 1
        results.append(result)
    overall = "FAIL" if summary["FAIL"] else ("PASS" if summary["PASS"] else "SKIP")
    return {"overall_status": overall, "summary": summary, "results": results}


def demo_payload():
    return {
        "issue_date": "2026-03-01",
        "core_date": "2026-03-08",
        "issue_date_chinese": "零叁月零壹日",
        "amount_numeric": "￥1680.32",
        "amount_upper": "人民币壹仟陆佰捌拾元零叁角贰分",
        "signatures": ["财务专用章", "法人章"],
    }


def cmd_build(_args):
    rule_df, align_df, atom_df, kg = build_catalog()
    paths = save_build(rule_df, align_df, atom_df, kg)
    print(f"已生成规则目录: {paths['rule_catalog']}")
    print(f"已生成规则-条款对齐: {paths['alignment']}")
    print(f"已生成聚焦 atom 子集: {paths['atom_catalog']}")
    print(f"已生成 KG JSON: {paths['kg_json']}")
    print(f"聚焦规则数: {len(rule_df)}")
    print(f"对齐条款数: {len(align_df)}")
    print(f"关联 atom 数: {len(atom_df)}")


def cmd_check(args):
    rule_catalog = PROC / "reference_rule_catalog.xlsx"
    if not rule_catalog.exists():
        rule_df, align_df, atom_df, kg = build_catalog()
        save_build(rule_df, align_df, atom_df, kg)
    else:
        rule_df = pd.read_excel(rule_catalog).fillna("")
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8")) if args.payload else demo_payload()
    report = run_check(payload, rule_df)
    out = PROC / "reference_rule_compliance_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"总结果: {report['overall_status']}")
    print(f"PASS={report['summary']['PASS']} FAIL={report['summary']['FAIL']} SKIP={report['summary']['SKIP']}")
    print(f"报告已保存: {out}")
    for item in report["results"]:
        print(f"[{item['status']}] {item['rule_id']} {item['expert_rule']} -> {item['reason']}")


def build_parser():
    parser = argparse.ArgumentParser(description="依据规则参考文件构建两部法规的聚焦 KG，并执行合规校验。")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build_p = sub.add_parser("build", help="构建规则目录、对齐结果、atom 子集和 KG JSON。")
    build_p.set_defaults(func=cmd_build)
    check_p = sub.add_parser("check", help="基于 7 条参考规则执行合规校验。")
    check_p.add_argument("--payload", help="可选：JSON 文件路径；不提供时使用内置演示样例。")
    check_p.set_defaults(func=cmd_check)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
