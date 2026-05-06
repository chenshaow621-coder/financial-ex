# src/mock_db_schema.py

# 这是一个模拟的金融业务系统数据字典
# 在实际项目中，这部分内容通常来自你们的元数据管理平台或 DDL 导出

BUSINESS_DATA_DICTIONARY = """
1. 核心实体表 (Tables)
-----------------------
[Table: Client] (客户表)
- client_id: 客户唯一标识
- client_type: 客户类型 (INDIVIDUAL-个人, ENTERPRISE-企业)
- kyc_status: 身份核验状态 (PASSED, PENDING, FAILED)
- risk_level: 风险等级 (LOW, MEDIUM, HIGH)

[Table: Bill] (票据表)
- bill_id: 票据号码 (主键)
- bill_type: 票据类型 (ACPT-银承, COMM-商承, CHQ-支票)
- amount: 票面金额 (Decimal)
- issue_date: 出票日期
- due_date: 到期日期
- status: 票据状态 (NORMAL-正常, LOSS_REPORTED-已挂失, FROZEN-冻结, PAID-已兑付)
- drawer_id: 出票人ID (关联 Client)
- holder_id: 持票人ID (关联 Client)
- cash_flag: 现金标识 (CASH-现金, TRANSFER-转账)

[Table: Transaction] (交易流水表)
- txn_id: 流水号
- bill_id: 关联票据
- txn_type: 交易类型 (ISSUE-出票, ENDORSE-背书, DISCOUNT-贴现, PAY-付款)
- txn_time: 交易时间

2. 业务操作接口 (APIs)
-----------------------
[API: report_loss] (挂失接口)
- input: bill_id, reason, applicant_id
- logic: 将 Bill.status 更新为 LOSS_REPORTED，并触发风控检查。

[API: stop_payment] (止付接口)
- input: bill_id, court_order_no
- logic: 锁定票据资金，禁止流转。
"""