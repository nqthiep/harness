# Harness Design — Safety Engine

**Phạm vi:** policy engine, vòng đời `Decision`, audit sink, thực thi taint hai chiều,
quarantine, và cơ chế giữ cho model không cầm công tắc nào.

Tệp này **mở rộng** [`00-foundation.md`](./00-foundation.md). Từ vựng, `Effect`, hai
lattice, `Decision`/`Scope`/`Actor` lấy nguyên ở đó — không đổi tên, không đổi mô hình.

Đây là phần sửa **phát hiện số một của toàn bộ nghiên cứu**:

> Qua Python, TypeScript và Java — 30 gói, 3 hệ sinh thái, mọi vendor lớn — **không gói
> nào mô hình hoá approval như một quyết định audit được.** Ở đâu nó cũng là *trạng thái
> quyền*. ([§10](../research/10-governance-health-languages.md) §28, tổng kết
> [§09](../research/09-memory-context-multiagent-hitl.md) §14,
> [§06](../research/06-typescript.md) Phát hiện 2)

---

## 0. Một lưu ý từ vựng bắt buộc

`00-foundation.md` §6 gán tên `Decision` cho **bản ghi phê duyệt**. Cài đặt hiện tại
trong `src/harness/policy/base.py` đang dùng cái tên đó cho *kết quả của policy*. Hai
khái niệm khác nhau và phải mang hai tên khác nhau:

| khái niệm | tên | ai tạo ra |
|---|---|---|
| phần tử lattice | `Verdict` | — |
| kết quả hợp thành của policy engine | **`Ruling`** | runtime, thuần tính toán |
| bản ghi phê duyệt bất biến | **`Decision`** | runtime + con người |

`Ruling` **không** là từ đồng nghĩa của `Decision`: nó không có `actor`, không tồn tại
sau lời gọi, không đi vào audit log như một quyền. Đây là đổi tên có chủ đích trong cài
đặt hiện tại, không phải thêm khái niệm mới.

---

## 1. PolicyEngine

### 1.1 Kiểu

```python
from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable


class Verdict(IntEnum):
    """Hợp thành bằng max(): một policy chỉ có thể thắt chặt (tính chất P-2)."""
    ALLOW = 0
    ASK = 1
    DENY = 2


@value
class Ruling:
    verdict: Verdict
    reason: str            # luôn có chữ; rỗng chỉ khi verdict is ALLOW mặc định
    policy: str            # policy nào phát ra — truy vết được về một dòng code
    scope: Scope | None    # policy đề xuất phạm vi grant nếu verdict is ASK


@value
class ToolCall:
    id: CallId
    name: ToolName
    arguments: Mapping[str, Any]
    spec: ToolSpec
    __hash__ = None        # giữ một Mapping — không hashable, cố ý


@runtime_checkable
class Policy(Protocol):
    name: str

    def check(self, call: ToolCall, ctx: PolicyContext) -> Ruling:
        """Thuần, đồng bộ, không I/O. Xem POL-4."""
```

`PolicyContext` là **view chỉ đọc** trên state đã checkpoint của run: `label`, `ledger`
(số dư, không phải object có `spend()`), `run_id`, `step`, `actor_of_run`. Nó không cầm
tham chiếu tới `PolicyEngine`, `DecisionLog`, hay `AuditSink` — xem §6.

### 1.2 Engine

```python
EFFECT_FLOOR: Mapping[Effect, Verdict] = {
    Effect.READ:     Verdict.ALLOW,
    Effect.WRITE:    Verdict.ASK,
    Effect.EXTERNAL: Verdict.ALLOW,
    Effect.DANGER:   Verdict.ASK,
}


class PolicyEngine:
    """Nằm trên đường đi bắt buộc (R-1). Không phải middleware, không cài được thì thôi."""

    def __init__(self, builtins: Sequence[Policy], user: Sequence[Policy] = ()) -> None:
        self._policies: tuple[Policy, ...] = tuple(builtins) + tuple(user)
        # builtins đứng trước và không có API nào gỡ được — không có remove_policy().

    def decide(self, call: ToolCall, ctx: PolicyContext) -> Ruling:
        worst = Ruling(EFFECT_FLOOR[call.spec.effect], "effect floor", "core.effect", None)
        for p in self._policies:
            try:
                r = p.check(call, ctx)
            except Exception as exc:                       # fail closed, luôn luôn
                r = Ruling(Verdict.DENY, f"policy {p.name!r} raised: {exc}", p.name, None)
            if r.verdict > worst.verdict:
                worst = r
            if worst.verdict is Verdict.DENY:
                break                                      # DENY là đỉnh lattice
        return worst
```

Bốn quyết định, mỗi cái sửa một khuyết điểm đo được:

**POL-1 — sàn theo `Effect`, không phải `ALLOW`.** Engine khởi tạo từ `EFFECT_FLOOR`, nên
một run **không có policy nào** vẫn hỏi trước khi `write`/`danger`.
*Sửa khuyết điểm:* ở Microsoft, gate là per-tool và phải suy lại đúng cho từng tool —
`_file_access.py` làm đúng, `mode_set` làm sai trong **cùng một module**
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2). Phân loại một lần rồi
suy ra gate thì không thể có mâu thuẫn đó.

**P-2 — hợp thành bằng `max()`, thêm policy không bao giờ nới.** §1.3 chứng minh.

**POL-3 — fail closed khi policy ném lỗi.** Đối lập với `threading.local()` của Microsoft:
lỗi ở đó **fail open và im lặng** ([§09](../research/09-memory-context-multiagent-hitl.md)
§16bis). Một policy hỏng ở đây thành `DENY` kèm tên policy trong `reason`.

**POL-4 — `Policy.check` là hàm thuần, đồng bộ.** Không `async`, không I/O, không gọi
model. Lý do có thể đo: (a) một hàm thuần enumerate được nên P-2 chứng minh được bằng
test tính chất chứ không bằng review — và nghiên cứu đã cho thấy 23 vòng *đọc* tìm ra
**0 lỗi bảo mật** còn 16 vòng *chạy* tìm ra **4** (foundation §5, R-2); (b) một policy
`async` có thể gọi model, và policy gọi model là model tự ảnh hưởng lên quyền của mình.
Phần cần `await` (hỏi người) không phải policy — nó ở §2.

### 1.3 Chứng minh P-2 bằng test, không bằng review

`Ruling` là dữ liệu và `check` là hàm thuần, nên toàn bộ `PolicyEngine` là một hàm
enumerate được. Bốn tính chất, viết bằng `hypothesis`:

```python
from hypothesis import given, strategies as st

verdicts = st.sampled_from(list(Verdict))
policies = st.builds(ConstPolicy, name=st.text(min_size=1), verdict=verdicts)


@given(base=st.lists(policies, max_size=6), extra=policies, call=tool_calls())
def test_p2_adding_a_policy_never_loosens(base, extra, call):
    before = PolicyEngine((), base).decide(call, CTX).verdict
    after = PolicyEngine((), [*base, extra]).decide(call, CTX).verdict
    assert after >= before


@given(base=st.lists(policies, max_size=6), call=tool_calls(), seed=st.integers())
def test_verdict_is_order_independent(base, call, seed):
    shuffled = random.Random(seed).sample(base, len(base))
    assert (PolicyEngine((), base).decide(call, CTX).verdict
            == PolicyEngine((), shuffled).decide(call, CTX).verdict)
    # `reason` CÓ THỂ khác nhau: short-circuit ở DENY đầu tiên. Chỉ verdict là bất biến.


@given(base=st.lists(policies, max_size=6), call=tool_calls())
def test_p1_effect_floor_holds(base, call):
    assert PolicyEngine((), base).decide(call, CTX).verdict >= EFFECT_FLOOR[call.spec.effect]


@given(base=st.lists(policies, max_size=6), call=tool_calls(), idx=st.integers(0, 5))
def test_p3_a_raising_policy_denies(base, call, idx):
    poisoned = [*base]
    poisoned.insert(min(idx, len(poisoned)), RaisingPolicy())
    assert PolicyEngine((), poisoned).decide(call, CTX).verdict is Verdict.DENY
```

Đây là điều foundation §3.1 đòi: *"Chứng minh được bằng test tính chất, không phải bằng
review."* Ba tính chất đầu là bất biến đại số của lattice; cái thứ tư là bất biến vận
hành. Cả bốn chạy trong CI, không phụ thuộc vào ai đọc diff.

Bổ sung ở tầng graph (thuộc [`04-runtime-durability.md`](./04-runtime-durability.md)):
`unguarded_paths()` chứng minh không đường nào tới node `tools` mà không qua node
`policy`. **Lý do cơ chế này phải tồn tại là một quan sát, không phải giả định**: "ship
middleware nhưng harness không install" đã xảy ra ở một vendor lớn
([§08](../research/08-tool-mcp-plugin.md) §24, [§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

---

## 2. Vòng đời `Decision`

### 2.1 Đường đi, từ `ASK` tới bản ghi

```
tool_call do model đề xuất
   │
   ├─(1) PolicyEngine.decide          → Ruling(ASK, scope=…)
   │
   ├─(2) DecisionLog.lookup(scope, now)
   │        ├─ tìm thấy grant còn hạn → dùng lại, KHÔNG hỏi lại, KHÔNG ghi Decision mới
   │        └─ không thấy             → tiếp
   │
   ├─(3) ApprovalProvider.ask(AskRequest) — chỗ DUY NHẤT được await một con người
   │
   ├─(4) runtime dựng Decision (bất biến D-1) — model không chạm vào bước này
   │
   ├─(5) AuditSink.commit(decision)  ← DURABLE TRƯỚC KHI TOOL CHẠY (§3)
   │
   └─(6) verdict ALLOW → tools node ; DENY → tool_result kiểu error, run tiếp
```

Bước (2) tồn tại trước (3) là điểm học của Microsoft: `ToolApprovalState` serialise được
và session-backed, nên grant sống qua resume thay vì bị *hỏi lại im lặng* hoặc *cấp lại
im lặng* ([§09](../research/09-memory-context-multiagent-hitl.md) §14).

### 2.2 Kiểu ở biên người-máy — học Vercel

```python
@value
class AskRequest:
    call: ToolCall
    ruling: Ruling
    reason: str                  # CHIỀU REQUEST: vì sao đang hỏi, hiển thị cho người duyệt
    proposed_scope: Scope        # policy đề xuất; người duyệt có thể thu hẹp, không nới
    max_grant: timedelta         # trần TTL của run này — xem §2.5


@value
class AskOutcome:
    verdict: Literal[Verdict.ALLOW, Verdict.DENY]   # ASK không bao giờ là kết quả
    actor: Actor                                     # provider phải nêu tên người
    reason: str | None                               # CHIỀU RESPONSE
    scope: Scope                                     # ⊆ proposed_scope, kiểm tra ở (4)
    grant_for: timedelta | None                      # None = chỉ lời gọi này


class ApprovalProvider(Protocol):
    async def ask(self, req: AskRequest) -> AskOutcome: ...
```

**Học điểm mạnh của ai:** `ToolApprovalStatus` của Vercel AI SDK là *hình dạng* approval
tốt nhất tìm được trong cả ba hệ sinh thái — union bốn trạng thái, có `approvalId`, và
`reason` chảy **hai chiều**: trên request để hiển thị cho người duyệt, trên response để
ghi lại ([§06](../research/06-typescript.md) Phát hiện 2). Hai trường `reason` ở trên là
đúng chi tiết đó.

**Khuyết điểm đang sửa:** Vercel dừng ở *status*. `AskOutcome` không phải kết quả cuối —
nó là **đầu vào** để runtime dựng `Decision`. Và `not-applicable` của Vercel ở đây được
biểu diễn bằng **sự vắng mặt của `Decision`**: không hỏi thì không có bản ghi phê duyệt,
chỉ có một event `policy.allowed` ở mức audit của `Effect`. Phân biệt "không cần hỏi" với
"đã hỏi và được đồng ý" giữ nguyên, nhưng bằng kiểu chứ không bằng một nhánh enum.

### 2.3 Ai ghi trường nào — bất biến D-1

```python
def _record(req: AskRequest, out: AskOutcome, *, clock: Clock, run_id: RunId) -> Decision:
    if not _scope_narrower_or_equal(out.scope, req.proposed_scope):
        raise PolicyViolation("approver widened the scope")     # người duyệt cũng không nới được
    ttl = _cap(out.grant_for, req.max_grant)
    return Decision(
        id=DecisionId(ulid_from(clock.now())),   # runtime
        verdict=out.verdict,                     # con người
        scope=out.scope,                         # policy đề xuất, người thu hẹp
        actor=out.actor,                         # provider — không có biến thể Model
        decided_at=clock.now(),                  # runtime, KHÔNG phải model
        expires_at=None if ttl is None else clock.now() + ttl,   # runtime
        reason=out.reason,                       # con người
        run_id=run_id,                           # runtime
    )
```

| trường | ai điền | model chạm được không |
|---|---|---|
| `id`, `decided_at`, `run_id` | runtime (`Clock` tiêm vào) | không |
| `verdict`, `reason` | con người / `Operator` | không |
| `scope` | policy đề xuất, người duyệt chỉ được **thu hẹp** | không |
| `actor` | `ApprovalProvider` | không — `Actor` không có biến thể `Model` |
| `expires_at` | runtime tính từ `grant_for` đã cap | không |

`_record` là hàm module-private, không nằm trong `harness.__init__`, và không tool nào
gọi được nó — xem §6.

**Khuyết điểm đang sửa (rất cụ thể):** `decision_log` của agno là tín hiệu "audit" dày
nhất trong 23 gói Python, và **mọi trường của nó do model viết**: `decision`,
`reasoning`, `decision_type`, `context`, `alternatives`, `confidence` — "there is no
field the runtime fills in and the model cannot"
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1). Bảng trên là phủ định
trực tiếp của câu đó.

### 2.4 Append-only — bất biến D-2

```python
class DecisionLog(Protocol):
    def append(self, decision: Decision) -> None: ...
    def lookup(self, call: ToolCall, *, run_id: RunId, now: datetime) -> Verdict | None: ...
    def since(self, run_id: RunId) -> Sequence[Decision]: ...
```

Không có `update`, không có `delete`, không có `revoke`. **Thu hồi = `append` một
`Decision` mới có `verdict=DENY` trên cùng `Scope`.** Điều đó đúng vì tra cứu hợp thành
bằng chính lattice:

```python
def lookup(self, call, *, run_id, now):
    hits = [d for d in self.since(run_id)
            if scope_matches(d.scope, call) and (d.expires_at is None or d.expires_at > now)]
    return max((d.verdict for d in hits), default=None)   # DENY thắng, theo max()
```

Một dòng `max()` cho ba tính chất: thu hồi luôn thắng grant, thứ tự append không ảnh
hưởng kết quả, và cùng một lattice của §1 — không có luật ưu tiên thứ hai để hiểu sai.

### 2.5 `expires_at`, và vì sao `always_approve` là sai

`_ApprovalRecord` của openai-agents lưu `approved: bool | list[str]`. Phần *scoped* là
đúng và đáng học: "yes to **this** call" khác "yes to this tool forever", và kiểu phân
biệt được hai cái đó. Phần sai là `approve_tool(item, always_approve=True)` ghi
`approved = True` và **grant đó không bao giờ hết hạn trong đời context**; không ghi ai
bấm, không ghi lúc nào ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

Ba chỗ sai, ba cách sửa:

1. **Vĩnh viễn không biểu diễn được.** Foundation quy ước `expires_at: None` nghĩa là
   *chỉ lời gọi này*, không phải *mãi mãi*. Muốn có standing rule thì phải nêu TTL rõ
   ràng. "Mãi mãi" **không có giá trị nào biểu diễn được** — đây là Poka-Yoke ở tầng
   kiểu, không phải ở tầng review.
2. **Trần TTL do vận hành đặt, không do UI approval đặt.** `RunConfig.max_grant_ttl`
   (mặc định 1 giờ, `danger` bị ép về `None` = chỉ lần này). `_cap()` ở §2.3 áp trần.
   Một UI phê duyệt bị lỗi hoặc bị điều khiển cũng không cấp được grant 100 năm.
3. **Grant không sống quá `run_id`.** `lookup` lọc theo `run_id`. Muốn grant xuyên run
   thì đó là `Operator` policy — một `Actor` khác, một đường khác, không phải một cú bấm
   Approve bị tái sử dụng.

Đồng hồ là `Clock` tiêm vào, không phải `datetime.now()` rải rác — nên hết hạn test được
xác định, và không có đường nào để một tool đẩy thời gian.

### 2.6 Khớp `Scope`

```python
def scope_matches(scope: Scope, call: ToolCall) -> bool:
    if scope.tool != call.name:
        return False
    if scope.server is not None and scope.server != call.spec.server:
        return False
    if scope.call_id is not None and scope.call_id != call.id:
        return False
    if scope.args is not None and scope.args != canonical_args(call.arguments):
        return False
    return True
```

Bốn trục, theo đúng thứ tự rẻ-đến-đắt:

- **`tool`** — mọi framework đều có. Không đủ.
- **`args`** — so sánh **bằng nhau toàn bộ mapping**, nên `args=None` khớp mọi lời gọi
  còn `args={}` khớp **chỉ** lời gọi không tham số. Đây là ternary của Microsoft giữ
  nguyên, kể cả phần tài liệu hoá: duyệt `delete_file(path="/tmp/x")` **không** duyệt
  `delete_file(path="/etc/passwd")`. Mọi dự án khác trong nghiên cứu duyệt *động từ* và
  bỏ qua *tân ngữ* ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
- **`server`** — biên tin cậy của MCP server. Grant cho một server không chuyển sang
  server khác có cùng tên tool. Đây là **phòng thủ confused-deputy duy nhất tìm thấy
  trong cả nghiên cứu**, và nó đáng được chép lại nguyên vẹn.
- **`call_id`** — có giá trị thì grant chết ngay sau lời gọi đó. Đây là phần đúng của
  openai-agents (`list[str]` các call id), giữ lại.

`canonical_args` là chuẩn hoá **cú pháp** (sắp khoá, JSON canonical, số về chuỗi), không
phải chuẩn hoá **ngữ nghĩa**. Chuẩn hoá ngữ nghĩa (`/tmp/../etc/passwd`) thuộc validator
của chính tool — đúng chỗ Microsoft làm tốt: `.`/`..` bị từ chối thẳng trong
`_file_access.py:183`, và `is_link_or_reparse_point` chặn symlink
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2). Hai lớp này bù nhau; xem
`## Chưa đủ evidence`.

---

## 3. Audit sink

### 3.1 Giao diện và bảo đảm

```python
@value
class AuditEvent:
    schema_version: int          # có version, nếu không thì không đổi taxonomy được
    run_id: RunId
    seq: int                     # đơn điệu TRONG một run
    at: datetime
    type: Literal["decision", "policy.denied", "flow.denied", "budget.denied", "tool.called"]
    payload: Mapping[str, Any]


class AuditSink(Protocol):
    def commit(self, event: AuditEvent) -> None:
        """Trở về khi đã DURABLE. Ném lỗi thì gọi bên coi là chưa ghi."""

    def emit(self, event: AuditEvent) -> None:
        """Best-effort, không chặn. Chỉ cho event mức debug/info."""
```

Hai phương thức chứ không một, vì hai bảo đảm khác nhau và gộp chúng lại là cách để mất
bảo đảm mạnh hơn một cách im lặng.

| câu hỏi | trả lời | vì sao |
|---|---|---|
| **Durable trước khi tool chạy?** | **Có, với mọi `Decision`.** `commit()` phải trả về trước khi runtime chuyển sang node `tools`. | Một phê duyệt chỉ có giá trị nếu nó tồn tại *trước* hệ quả. Bản ghi ghi sau khi email đã gửi trả lời được "đã xảy ra gì" nhưng không trả lời được "ai cho phép". |
| **Thứ tự?** | Đơn điệu **theo `run_id`** qua `seq`; toàn cục chỉ sắp xếp theo ULID trong `Decision.id` (thời gian). | Thứ tự toàn cục nghiêm ngặt đòi một điểm đồng bộ trong quá trình chạy — cái giá đó không sửa một khuyết điểm đo được nào. KISS. |
| **Mất mát?** | `commit()` lỗi ⇒ **`DENY`, tool không chạy.** Fail-closed, không có cờ tắt. | Bất biến đang bảo vệ là *mọi hành động cần phê duyệt đều có bản ghi*. Chạy tool khi không ghi được bản ghi là phá đúng bất biến đó. |
| **`emit()` lỗi?** | Nuốt, đếm vào một counter, không chặn run. | Nó chỉ mang `read`-level event; mất một dòng log không phải lỗi bảo mật. |

`AuditSink` là **plugin seam** (OTel, file, DB — theo `05-ideal-harness` §33), nhưng
*việc gọi `commit` trước khi chạy tool* là code trên đường đi bắt buộc, không phải
middleware. Đúng ranh giới R-1: **plugin cho policy, đường đi bắt buộc cho invariant**
([§08](../research/08-tool-mcp-plugin.md) §24).

### 3.2 Vì sao `decision_log` của agno sai kiến trúc

Không phải vì nó dở — nó làm tốt việc `agno.learn` cần. Sai ở chỗ **nó bị đặt tên và
được đọc như một audit log**, trong khi:

1. **Đường ghi là một tool mà model gọi.** `_build_log_decision_tool` trao cho model một
   `log_decision(...)`. Model bỏ qua lời gọi ⇒ **không có dấu vết nào cả**. Một audit log
   mà bên bị audit chọn được có ghi hay không thì không phải audit log.
2. **Mọi trường là văn xuôi do model viết.** Không trường nào runtime điền mà model không
   sửa được ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
3. **`AGENTIC` là mode duy nhất**, cưỡng chế trong `__post_init__`: đặt mode khác thì nó
   log warning rồi vẫn chạy AGENTIC.

Ở đây: `AuditSink` không có tool nào bind vào nó (§6), `commit` được gọi bởi runtime tại
một chỗ trên graph, và mọi trường định danh do runtime điền. Bên bị audit không có bút.

---

## 4. Thực thi taint hai chiều

### 4.1 Nhãn và luật

```python
# Integrity / Confidentiality / Label: định nghĩa chuẩn ở 00-foundation.md §3.2.
# Ở đây chỉ dùng, không định nghĩa lại.
from harness import Integrity, Confidentiality, Label
```

`join` là `max()` từng trục — **cùng một phép hợp thành với `Verdict` ở §1**. Đơn điệu,
không bao giờ giảm trong một run. Một phép toán để hiểu, không phải hai.

```python
def check_flow(label: Label, spec: ToolSpec) -> Ruling:
    if (label.integrity is Integrity.UNTRUSTED
            and spec.effect is Effect.DANGER
            and not spec.accepts_tainted):
        return Ruling(Verdict.DENY, "untrusted context may not drive a danger tool",
                      "core.flow.integrity", None)
    if (label.confidentiality is Confidentiality.SECRET
            and spec.max_confidentiality is Confidentiality.PUBLIC):
        return Ruling(Verdict.DENY, "secret context may not reach a public sink",
                      "core.flow.confidentiality", None)
    return Ruling(Verdict.ALLOW, "", "core.flow", None)


def label_after(spec: ToolSpec, current: Label) -> Label:
    return current.join(spec.emits)   # external ⇒ Label(UNTRUSTED, …) theo foundation §2
```

`read` không bị luật confidentiality chạm tới vì nó không phải sink — đây là nhận định
đúng của Microsoft, chép lại có ghi công: read-only tool "safe to call even when the agent
context is tainted — it cannot exfiltrate" (`security.py:3033`,
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

Tool đến từ MCP mà **không khai `effect`** nhận lớp untrusted nhất, không phải lớp an
toàn nhất: `Effect.EXTERNAL` + `max_confidentiality=PUBLIC` + `accepts_tainted=False`.
Protocol không phải security boundary ([§05](../research/05-ideal-harness.md) §33).

### 4.2 Cài ở đâu trên đường đi

Đúng **hai** điểm, cả hai là node của graph đã compile:

```
model ──► policy ──► tools ──► label ──► model
           │  ▲                  │
           │  └── check_flow()   └── label_after()   (ghi vào state đã checkpoint)
           └───── PolicyEngine.decide() ∨ check_flow()  ⇒ max()
```

1. **Trước khi chạy** — trong node `policy`, `check_flow(label, spec)` hợp thành với
   `PolicyEngine.decide(...)` bằng đúng `max()`. Không có đường vòng: một `DENY` từ flow
   không phân biệt được với một `DENY` từ policy, và cả hai đều là đỉnh lattice.
2. **Sau khi chạy** — node `label` gọi `label_after` và ghi nhãn mới vào state **trước
   khi** kết quả tool được merge vào `messages`. Nếu ghi nhãn nằm sau merge thì có một
   cửa sổ trong đó context đã bẩn mà nhãn còn sạch.

**Vì sao không tắt được bằng cách không cài plugin:** cả hai là node bắt buộc trong graph,
và `unguarded_paths()` từ chối compile nếu có đường tới `tools` không qua `policy` hoặc
đường từ `tools` về `model` không qua `label` (R-2, foundation §5). Không có cờ
`enable_taint=`. Không có `install_middleware()`. Cách duy nhất để bỏ nó là sửa graph
builder trong core và làm hỏng một test biết đếm.

### 4.3 Ba chỗ phải khác Microsoft

`agent_framework/security.py` là kiến trúc an toàn tinh vi nhất trong toàn nghiên cứu —
IFC hai chiều thật, `combine_labels` đơn điệu, MCP annotation → nhãn. Nó cũng là ví dụ rõ
nhất cho khoảng cách giữa *làm được gì* và *mặc định làm gì*
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

| # | Microsoft | ở đây | bằng chứng |
|---|---|---|---|
| **1. Bật mặc định** | submodule opt-in; `grep` import từ `_harness/` trả về **rỗng**; không có trong `agent_framework/__init__.py`; `create_harness_agent` không cài lattice | node bắt buộc trong graph; `unguarded_paths()` chứng minh; không có công tắc bật/tắt | §16bis "the good module is not wired in" |
| **2. Không `threading.local()`** | `_current_middleware = threading.local()` set/clear xuyên `await`; hai tool call đồng thời đọc nhầm slot của nhau, hoặc đọc `None` — **im lặng, fail-open** | nhãn sống trong **state đã checkpoint của thread**, gắn **theo từng message** (`msg.label_integrity`, `msg.label_confidentiality` — chuỗi, JSON-checkpointable); nhãn hiệu dụng là `join` tính lại theo L-3 ([00 §3.2](00-foundation.md)), không phải một biến tích luỹ | §16bis, và Round 34/37/41 của chính repo này |
| **3. Không singleton toàn tiến trình** | `_global_variable_store` và `_quarantine_chat_client` ở mức module, đổi qua setter `global`; đa tenant thì một tenant đổi là mọi tenant đổi | mọi trạng thái an toàn khoá theo `run_id` trong state; không có biến module nào ghi được sau import | §16bis |

Về (2), một điểm quan trọng dễ bị hiểu nhầm: `contextvars.ContextVar` — primitive đúng mà
§16bis đề xuất — **vẫn không đủ ở đây**, vì mỗi node LangGraph chạy trong một context được
copy nên `ContextVar` không đi xuyên node. Đây không phải suy đoán: repo này đã mắc đúng
lỗi đó ở Round 34, **sửa sai** ở Round 37, và chỉ đúng ở Round 41 khi trạng thái được đưa
vào state đã checkpoint (foundation §5, R-4). `threading.local()` yếu hơn hẳn primitive
mà chính chúng ta đã chứng minh là chưa đủ.

---

## 5. Quarantine model — ĐÃ CẮT, chuyển sang mục rủi ro

Mục này từng đặc tả một model phụ rẻ hơn để suy luận trên nội dung `UNTRUSTED`
(mẫu dual-LLM/CaMeL), cùng `Quarantined[T]` và một nhánh fail-closed riêng.

**Đã cắt theo chính luật biên tập của bản thiết kế** ([00 §8.4](00-foundation.md)): mẫu này
có đúng **một** cài đặt trong toàn nghiên cứu, và cài đặt đó `@experimental`, không được wire
vào harness của chính nó, và không concurrency-safe. **Không có eval nào** so sánh tỉ lệ
prompt-injection thành công có và không có quarantine
([review-kiss.md](review-kiss.md) K-1).

Đường đi bình thường đã đủ và đã có bằng chứng: `UNTRUSTED` + `danger` ⇒ `ASK` (có người
duyệt) hoặc `DENY`. Đặc tả đầy đủ được giữ ở
[`07-risks-and-open-issues.md`](07-risks-and-open-issues.md) dưới mục *"ý tưởng có kiến
trúc, chờ eval"* — cắt khỏi đường đi bắt buộc, không vứt đi.


## 6. Model không cầm công tắc nào (R-3)

### 6.1 Danh sách cụ thể

| model **không bao giờ** làm được | cơ chế đảm bảo (không phải prompt) |
|---|---|
| Ghi hoặc sửa một `Decision` | `Actor` không có biến thể `Model` — bản ghi do model tạo **không dựng được về mặt kiểu**. `_record()` là module-private, không export. |
| Đổi `Verdict` hoặc thêm/bớt `Policy` | `PolicyEngine._policies` là `tuple` chốt lúc dựng `Runtime`; không có `add_policy`/`remove_policy`. `PolicyContext` mà tool nhận không cầm tham chiếu tới engine. |
| Đổi chế độ an toàn (kiểu `mode_set`) | Chế độ an toàn không phải tool. Nó là trường của `RunConfig` bất biến, chốt trước lượt đầu tiên. Không có hàm nào đổi nó sau khi `Run` bắt đầu. |
| Tự nới `expires_at` hoặc kéo dài grant | `expires_at` do runtime tính từ `Clock` tiêm vào, cap bằng `RunConfig.max_grant_ttl`. |
| Ghi vào `AuditSink` | Không có tool nào bind tới sink. Sink chỉ được gọi từ node của graph. |
| Hạ nhãn taint | `Label.join` chỉ có `max()`; không tồn tại hàm giảm nhãn. |
| Đổi hoặc nâng budget | `Ledger` sống trong state đã checkpoint; `PolicyContext` chỉ phơi **số dư dạng số**, không phơi object có `spend()`. |
| Gọi một tool ngoài registry | Tên tool tra trong registry đóng; tên lạ ⇒ lỗi cứng, không phải lookup động. |

### 6.2 Vì sao đây là cơ chế chứ không phải lời hứa

Bốn lớp, xếp từ mạnh xuống:

1. **Không biểu diễn được.** `Actor` không có `Model`; "grant vĩnh viễn" không có giá trị
   biểu diễn. Cái không dựng được thì không cần canh.
2. **Không tới được.** Control plane không nằm trong `ToolContext` mà tool nhận. Không có
   tham chiếu thì không có lời gọi.
3. **Không kê khai.** Danh sách schema gửi cho model **bằng đúng** allowlist của registry.
   Hàm của control plane không phải tool nên không có schema nào để model nêu tên.
4. **Chứng minh bằng test.**

```python
def test_no_agent_visible_tool_reaches_the_control_plane():
    forbidden = {"harness.policy.engine", "harness.audit", "harness.decision",
                 "harness.budget.ledger"}
    for spec in registry.agent_visible():
        assert not (_module_closure(spec.fn) & forbidden), spec.name


def test_schema_list_equals_the_allowlist():
    assert {t["name"] for t in registry.schemas_for_model()} == set(registry.agent_visible_names())


def test_actor_has_no_model_variant():
    assert "Model" not in {t.__name__ for t in typing.get_args(Actor)}
```

### 6.3 Khuyết điểm đang sửa, nêu đích danh

`_mode.py:289` của Microsoft:

```python
@tool(name="mode_set", approval_mode="never_require")
def mode_set(mode: str) -> str:
    """Switch the agent's operating mode."""
```

`plan` là chế độ chỉ-đọc, hỏi-trước — tức một **tư thế an toàn**. Model cầm công tắc, và
`approval_mode="never_require"` làm công tắc đó **không gate được về mặt cấu trúc**. Thứ
duy nhất đứng giữa model và việc rời chế độ plan là một câu tiếng Anh trong system prompt:
*"Only use mode_set if the user explicitly instructs/allows you to change modes."*
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2)

Đây đúng là định nghĩa **safety by prompt**, mà nghiên cứu xếp là anti-pattern quan sát
được của cả ngành ([§04](../research/04-weaknesses-antipatterns.md) §28).

Và bài học không phải "Microsoft cẩu thả" — cùng module ấy còn có `_file_access.py` bật
approval mặc định và tách read/write, tức thiết kế đúng. Bài học là: **enforcement
per-tool phải được suy lại đúng cho từng tool, và tool thứ hai mươi là chỗ nó trượt.** Ở
đây tư thế an toàn không phải tool, nên không có tool thứ hai mươi để trượt.

---

## 7. Bảng đối chiếu: khuyết điểm đo được ↔ cơ chế

| khuyết điểm quan sát được | ở đâu | cơ chế ở tệp này |
|---|---|---|
| Approval là boolean, không có actor/thời điểm/hạn dùng | 30 gói, 3 hệ sinh thái ([§10](../research/10-governance-health-languages.md) §28) | `Decision` bất biến, D-1 phân vai ghi trường (§2.3) |
| `always_approve` không bao giờ hết hạn | openai-agents ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1) | "vĩnh viễn" không biểu diễn được + `max_grant_ttl` (§2.5) |
| Audit log do bên bị audit viết bằng tool | agno `decision_log` (§14.1) | sink không có tool bind; runtime điền trường định danh (§3.2) |
| Approval khoá theo *động từ*, bỏ qua *tân ngữ* | toàn bộ trừ Microsoft (§14.1) | `Scope.args` so khớp bằng nhau toàn mapping (§2.6) |
| Grant chuyển nhầm giữa hai MCP server cùng tên tool | chỉ Microsoft phòng thủ (§14.1) | `Scope.server` (§2.6) |
| Module an toàn tốt nhưng **không được wire vào** | Microsoft `security.py` (§16bis) | node bắt buộc + `unguarded_paths()` (§4.2) |
| `threading.local()` xuyên `await` ⇒ fail-open im lặng | Microsoft (§16bis) | nhãn trong state đã checkpoint (§4.3) |
| Singleton mức module trong server đa tenant | Microsoft (§16bis) | quarantine + nhãn thuộc `Run` (§5.2) |
| Model cầm công tắc chế độ an toàn | Microsoft `mode_set` (§14.2) | R-3: không biểu diễn / không tới được / không kê khai / có test (§6) |
| `ToolConfirmation` là một `boolean` | google-adk-java ([§10](../research/10-governance-health-languages.md) §28) | như hàng 1 — cùng một cách sửa cho cả ba ngôn ngữ |
| Approval nhầm là isolation | Goose ([§03](../research/03-safety-reliability.md) §17) | tệp này **không** tuyên bố isolation; sandbox là seam riêng ([§05](../research/05-ideal-harness.md) §33) |

Học điểm mạnh của ai, tóm tắt: **Microsoft** cho `Scope` (args + server_label + serialise
qua resume) và cho IFC hai chiều; **Vercel AI SDK** cho hình dạng approval bốn trạng thái
và `reason` hai chiều; **openai-agents** cho grant theo `call_id`; **google-adk-java** cho
mặc định `confirmed(false)` — fail-closed lúc dựng; **LangGraph** cho id định vị chỗ dừng,
được dùng ở [`04-runtime-durability.md`](./04-runtime-durability.md).

---

## Chưa đủ evidence

- **Chuẩn hoá tham số cho `Scope.args`.** So khớp bằng chuỗi canonical chặn được
  `delete_file(path="/etc/passwd")` sau khi duyệt `/tmp/x`, nhưng **không** chặn được hai
  chuỗi khác nhau trỏ cùng một tài nguyên (`/tmp/../etc/passwd`, symlink, tên host có
  Unicode đồng hình). Thiết kế hiện tại đẩy việc đó cho validator của từng tool — cùng chỗ
  Microsoft làm đúng — nhưng nghiên cứu **không đo** được lớp lỗi này còn sót bao nhiêu
  trong thực tế. Cần một vòng chạy thật để biết.
- **Chi phí của `commit()` đồng bộ.** Yêu cầu "durable trước khi tool chạy" thêm một
  round-trip lưu trữ vào mỗi tool cần phê duyệt. Nghiên cứu không có số đo độ trễ audit
  sink của bất kỳ gói nào (`langgraph` OTel 0,1; MS AF 7,7 là *mật độ mã*, không phải
  hiệu năng — [§03](../research/03-safety-reliability.md) §18). Chưa biết ngưỡng nào là
  không chấp nhận được.
- **`ApprovalProvider` thực tế trông thế nào.** Vercel là bằng chứng duy nhất có ai đó
  thật sự dựng UI phê duyệt (`reason` hai chiều là dấu vết của việc đó —
  [§06](../research/06-typescript.md)). Không có dữ liệu về việc con người dùng một hàng
  đợi phê duyệt ra sao ở quy mô: tỉ lệ bấm Approve theo phản xạ, thời gian chờ, hành vi
  khi hết hạn. `max_grant_ttl` mặc định 1 giờ là **phỏng đoán có lý, không phải số đo**.
- **Quarantine có giảm rủi ro thật không.** Mẫu dual-LLM/CaMeL chỉ có **một** cài đặt
  trong toàn nghiên cứu, và nó `@experimental`, không được wire vào, và không
  concurrency-safe (§16bis). Không có eval nào so sánh tỉ lệ prompt-injection thành công
  có và không có quarantine. Thiết kế ở §5 là **sửa chỗ đặt sai của một ý tưởng chưa được
  chứng minh** — nên nó fail-closed và tuỳ chọn, không phải mặc định.
- **Multi-tenancy.** Nghiên cứu ghi rõ "Chưa đủ evidence cho phần lớn thư viện"
  ([§03](../research/03-safety-reliability.md) §16). §4.3 và §5.2 khoá trạng thái theo
  `run_id`, đủ để tránh lỗi cụ thể đã quan sát ở Microsoft, nhưng **không** phải một mô
  hình tenancy đầy đủ (quota, cách ly lưu trữ, ranh giới định danh). Đó là việc của tầng
  service.
- **`Confidentiality` chỉ có hai bậc.** Foundation dùng `PUBLIC < SECRET`. Không gói Python
  nào trong nghiên cứu có kiểu `Secret` chuyên dụng chống rò rỉ qua log/prompt
  ([§03](../research/03-safety-reliability.md) §16), nên **không có bằng chứng** về việc
  hai bậc là đủ hay thiếu. Thêm bậc thứ ba khi và chỉ khi có một lớp lỗi đo được mà hai
  bậc không diễn đạt nổi.
- **Trần TTL cho `danger` ép về `None`** (chỉ lần này) là quyết định thận trọng, không
  phải kết luận từ dữ liệu. Không nguồn nào trong nghiên cứu đo tần suất phê duyệt lại
  làm người vận hành mệt tới mức bỏ đọc.
