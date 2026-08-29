# Day27-HITL

LangGraph workflow đánh giá churn risk của khách hàng, kết hợp agent
reasoning, confidence routing, hard policy rules, human approval bằng
Streamlit và audit logging.

## Cấu trúc project

```
day27-hitl/
├── app.py            # Streamlit UI - approval console, resume graph
├── graph.py           # GraphState, agent nodes, routing, graph compilation
├── models.py           # AuditEntry (Pydantic) + audit_log.json helpers
├── audit_log.json       # Audit trail (append-only)
└── requirements.txt
```

## Cách cài dependency

```bash
pip install -r requirements.txt
```

## Cách chạy LangGraph workflow

Có thể chạy graph độc lập bằng Python thuần, không cần Streamlit:

```python
from graph import build_graph

graph = build_graph()
config = {"configurable": {"thread_id": "demo-1"}}

result = graph.invoke({"customer_id": "CUST004"}, config)
print(result["proposed_action"], result["confidence_score"])

# Nếu bị interrupt (route tới execute_high_risk_action), state sẽ đứng ở đây:
print(graph.get_state(config).next)  # ('execute_high_risk_action',) nếu đang chờ duyệt

# Resume sau khi có quyết định của người review:
graph.update_state(config, {"human_decision": "approve", "reviewer_id": "operator_01"})
final = graph.invoke(None, config)
print(final["final_status"])
```

## Cách chạy Streamlit UI

```bash
pip install -r requirements.txt
streamlit run app.py
```

Trong sidebar, chọn một khách hàng mock (`CUST001`-`CUST005`) và bấm **Run
Evaluation**. Agent sẽ đánh giá TOI + churn probability và đề xuất action.
Tuỳ vào routing rule, action tự động thực thi (xuất hiện ở **Completed
Actions**) hoặc bị đẩy vào **Pending Approvals** chờ người review.

## Confidence threshold đang sử dụng

`CONFIDENCE_THRESHOLD = 0.85` (định nghĩa trong [graph.py](graph.py)). Action
low-risk (`send_email`) chỉ được auto-execute khi `confidence_score >= 0.85`;
thấp hơn ngưỡng này thì bị escalate sang human review dù là action low-risk.

## Hard policy rule

Action `increase_credit_limit` (định nghĩa trong tập `HIGH_RISK_ACTIONS` ở
[graph.py](graph.py)) **luôn luôn** phải qua human review, bất kể
`confidence_score` là bao nhiêu (kể cả 0.99). Trong `route_action`, rule này
được kiểm tra **trước** confidence threshold nên không bao giờ bị confidence
cao ghi đè - đây là lý do gọi là "hard" policy override.

## Cách Approve, Reject và Edit

Trong mục **Pending Approvals** của Streamlit UI, mỗi action card có 3 nút:

- **Approve** - đồng ý với `proposed_action` của agent, thực thi nguyên trạng.
- **Reject** - từ chối, action bị abort, không thực thi gì cả.
- **Edit** - mở popover cho phép sửa lại nội dung action trước khi thực thi
  (ví dụ đổi hạn mức credit limit đề xuất).

Cả 3 nút đều update state rồi resume graph để chạy `execute_high_risk_action`
(node bị `interrupt_before` chặn lại trước đó):

```python
graph.update_state(config, {"human_decision": decision, ...})
graph.invoke(None, config)
```

`execute_high_risk_action` đọc `human_decision` để quyết định thực thi
(`approve`/`edit`) hay abort (`reject`), rồi ghi kết quả vào audit log.

## Audit log được lưu ở đâu

Lưu tại [audit_log.json](audit_log.json), cùng cấp thư mục với `app.py`. Đây
là một JSON array append-only: mỗi quyết định (auto-execute lẫn human
approve/reject/edit) tạo thêm một `AuditEntry` mới vào cuối mảng, không ghi
đè lịch sử cũ (xem `append_audit_entry` trong [models.py](models.py)).
Streamlit UI hiển thị toàn bộ log này trong bảng **Audit Log** ở cuối trang.

Mỗi entry gồm: `timestamp`, `agent_id`, `action`, `confidence`,
`reviewer_id`, `decision` (`auto_approve` | `approve` | `reject` | `edit`).
