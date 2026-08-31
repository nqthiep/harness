# Rủi ro, đánh đổi, và vấn đề còn mở

Tệp này tồn tại vì luật §45 của nghiên cứu: **thà nói "Chưa đủ evidence" còn hơn đoán.**
Áp lên chính bản thiết kế, nó có nghĩa: mọi thứ chưa chắc phải nằm ở đây, không nằm rải rác
trong sáu tệp kia dưới dạng câu văn tự tin.

---

## 1. Phát hiện review CHƯA được sửa

Hai vòng review đối kháng cho **58 phát hiện**. Đã sửa: 5 lỗi chặn phát hành và 16 lỗi nhất
quán. **Còn lại dưới đây chưa sửa** — liệt kê đầy đủ, vì một danh sách rủi ro chỉ có giá trị
khi nó thành thật.

### 1.1 Bảo mật — nghiêm trọng, chưa sửa

| mã | vấn đề | vì sao chưa sửa |
|---|---|---|
| S-6 | quan hệ giữa `lookup()` và `Ruling`/`check_flow` không định nghĩa | cần chốt thứ tự hợp thành; ảnh hưởng cả 02 và 04 |
| S-7 | `Scope.server=None` là wildcard; `ServerIdentity.fingerprint` không tới được chỗ so khớp | sửa đúng cần đổi `Scope`, chạm 3 tệp |
| S-8 | lập luận chống rug-pull ở `03 §5.4` không đứng vững — args không đổi thì scope cũ vẫn khớp | cần cơ chế mới, không chỉ sửa văn |
| S-9 | server `trusted` **hạ** được effect qua re-list | cần luật đơn điệu cho re-classification |
| S-10 | `proposed_scope` mặc định không quy định ⇒ grant có thể thành verb-level | cần chốt mặc định an toàn |
| S-11 | `Actor` là lời tự khai, không có evidence | cần mô hình xác thực người duyệt |
| S-12 | ba kênh resume (`answer=`, `ResumeToken`) vẫn chưa thống nhất giữa 01 và 04 | đã sửa một nửa (`ruling=` → `answer=`) |
| S-13 | `slice_for_child` nhân bội `steps`/`wall_clock`, mâu thuẫn `04 §7.2` | cần chốt ngữ nghĩa sub-agent budget |
| S-14 | `reserve()` không có ngữ nghĩa với `spent`, không có đường huỷ reservation | cần đặc tả vòng đời `Reservation` |
| S-15 | `Policy` dùng chung mọi run; không luật nào cấm state trong `self` ⇒ R-4 có lỗ | sửa được bằng một câu luật + test |
| S-17 | `description` của tool MCP vào prompt khi nhãn còn `TRUSTED` | injection qua metadata; cần gắn nhãn cho description |
| S-18 | P-4 (`Policy.check` thuần) làm `DenyHosts` chỉ còn advisory ⇒ SSRF đi qua | cần tách policy thuần khỏi enforcement I/O |

> **S-16, S-19, và S-3 ĐÃ SỬA — trên giấy VÀ trong `src/harness/`.** Bước 0 chốt mô hình
> (S-16: `accepts_tainted` rời `@tool`, chỉ đến từ operator; S-19: nhãn per-message +
> L-1/L-2/L-3). Bước sau đó đưa vào code: `policy/label.py` (canonical `Integrity` ×
> `Confidentiality` × `Label`, `Grants`), `policy/builtin.py` (`check_flow` hai nhánh,
> `emits_of`), `lg/runtime.py` (`_effective_label` thay `_tainter`, L-2 stamp trong
> `call_model`, L-1 stamp trong `_run_tools`, `_manage` giữ `additional_kwargs` khi xoá
> nội dung). Backend cổ điển (`run.py`/`dispatch.py`) nâng `TaintTracker` lên `Label` hai
> trục nhưng GIỮ sticky-per-run — nó miễn nhiễm với chính kiểu rửa taint mà per-message
> phải phòng, vì nó không tính lại theo message; đây là khác biệt có chủ ý giữa hai
> backend, không phải việc chưa xong. `Secret[T]` (nguồn nâng confidentiality thứ nhất,
> S-3) **chưa cài** — chỉ có nguồn thứ hai (`Grants.sensitive`, operator đánh dấu tool).
> 11 test tấn công mới ở `tests/test_attack_s19.py`, mỗi cơ chế chính có một mutation test
> đi kèm (xoá đúng dòng code thì test phải đỏ) — cùng kỷ luật với bước 1/2.

### 1.2 Bảo mật — nên sửa (S-21…S-29)

`_blocked` không snapshot · `reserve()` bỏ qua cache-write · `call_key` thiếu domain
separator · `seq` không có nguồn cấp · UI injection và approval fatigue qua `args` ·
`canonical_args` mất kiểu · cửa sổ nhãn trong node `tools` · sub-agent cần `ASK` không có
đường · tái dùng grant không để lại bản ghi durable.

### 1.3 KISS — chưa cắt

`Confidentiality` giờ đã có nguồn (S-3 đã sửa) nên **K-6 không còn hiệu lực**. Còn lại:
`Reservation.exact` (K-7) · `run`/`try_run`/`raise_for_status`/`.ok` bốn cách nói một điều
(K-9) · taxonomy OTel 9 span nên rút còn 4 (K-10) · `end_strategy` (K-11) ·
`ServerIdentity.fingerprint` (K-12) · 38 mã bất biến với 3 va chạm namespace (K-13) ·
tuyên bố "14 tên, một import" sai — ví dụ import 20 tên từ 4 module (K-22) · 9 tunable
không có đường từ API công khai (K-23) · thiếu session/tenant identity mà minimal core đòi
(K-28).

### 1.4 Đếm khái niệm — chưa đạt mục tiêu

| phép đo | hiện tại | sau khi áp phần còn lại |
|---|---:|---:|
| `class` định nghĩa | ~55 | ~48 |
| tên để viết agent đầu tiên | **8** ✅ | 8 |
| tên cộng dồn tới production | ~38 | ~33 |

Mức 0 là 5 dòng và 8 tên — đạt yêu cầu "học sinh 10 tuổi". Đường tới production thì **chưa**
gọn như tuyên bố.

---

## 2. Ý tưởng có kiến trúc, chờ eval

Cắt khỏi đường đi bắt buộc theo luật §8.4, **không vứt đi**. Nếu có ngày đo được, đây là chỗ
lấy lại.


### Quarantine model (dual-LLM / CaMeL)

### 5.1 Cái Microsoft làm đúng, và chỗ đặt sai

`set_quarantine_client` cung cấp một model riêng, rẻ hơn, để suy luận trên nội dung không
tin cậy — mẫu dual-LLM / CaMeL. Đây là thứ **không gói nào khác trong 28 gói Python +
TypeScript có** ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Ý tưởng
đúng; chỗ đặt sai: `_quarantine_chat_client` là biến **mức module**, gán qua
`global`.

### 5.2 Nó thuộc về đâu trong đời một `Run`

Quarantine không phải cấu hình của tiến trình. Nó là **một seam của `Run`**:

```python
@value
class RunConfig:
    ...
    quarantine: ModelProvider | None = None      # None = fail closed, xem dưới
    max_grant_ttl: timedelta = timedelta(hours=1)


@value
class Quarantined[T]:
    """Kết quả rút trích từ nội dung UNTRUSTED. Chỉ dữ liệu có schema, không văn xuôi."""
    value: T                    # T phải là kiểu đóng: primitive, enum, hoặc @value có schema
    label: Label                # luôn Integrity.UNTRUSTED — không hạ được
    source_call_id: CallId
```

Ba luật, mỗi luật sửa một chỗ:

1. **Vòng đời = vòng đời `Run`.** Provider được chốt (resolve) một lần lúc `Run` khởi
   tạo và nằm trong `RunConfig` bất biến. Không setter, không `global`. Hai tenant chạy
   song song trong một tiến trình có hai `RunConfig` — sửa khác biệt #3 ở §4.3.
2. **Chỉ dữ liệu có schema quay lại context chính.** Model quarantine đọc nội dung
   `UNTRUSTED` và trả về `Quarantined[T]` với `T` là kiểu đóng. Văn xuôi tự do **không**
   được quay lại — đó chính là tính chất làm nên CaMeL: nội dung không tin cậy được rút
   thành *giá trị*, không được rút thành *chỉ thị*. Transcript của quarantine không bao
   giờ merge vào `messages` của run chính.
3. **`Quarantined.label` không hạ được.** Không có API nào biến `UNTRUSTED` thành
   `TRUSTED`. Quarantine giảm *bề mặt tấn công* (model rẻ, output có schema), nó **không**
   tẩy nhãn. Đây là điểm dễ cài sai nhất và là lý do trường `label` nằm ngay trong kiểu.
4. **Không cấu hình ⇒ `DENY`, không phải bỏ qua.** `quarantine=None` mà gặp tình huống
   cần nó thì kết quả là `DENY` kèm reason nêu rõ. Đối lập với Microsoft: ở đó module
   không được wire vào nên tình huống ấy **im lặng đi tiếp**.

Chi phí của model quarantine tính vào **cùng một `Ledger`** của run
([`05-cost-and-memory.md`](./05-cost-and-memory.md)). Một cơ chế an toàn có ngân sách
riêng là một cơ chế an toàn không đếm được.

**KISS:** quarantine chỉ được gọi ở đúng một tình huống — context `UNTRUSTED`, tool cần
gọi là `danger` hoặc `accepts_tainted=False`, và người vận hành đã cấu hình provider. Nếu
không, đường đi bình thường là `ASK` (có con người) hoặc `DENY`. Không thêm chế độ nào
khác cho tới khi có số đo cho thấy cần.

---

**Vì sao cắt.** Mẫu này có đúng **một** cài đặt trong toàn nghiên cứu, và cài đặt đó
`@experimental`, không được wire vào harness của chính nó, và không concurrency-safe. **Không
có eval nào** so sánh tỉ lệ prompt-injection thành công có và không có nó
([review-kiss.md](review-kiss.md) K-1).

**Điều kiện lấy lại:** một eval trên tập prompt-injection thật, đo tỉ lệ thành công có và
không có quarantine, trên cùng bộ tool. Nếu chênh lệch không có ý nghĩa thống kê, giữ nguyên
trạng thái cắt.

---

## 3. Đánh đổi đã chọn, và cái giá của từng cái

| chọn | được | mất |
|---|---|---|
| Graph thay vì loop | durability, chứng minh được, vẽ được — ba hệ quả của một quyết định | phụ thuộc LangGraph; người dùng phải hiểu khái niệm node |
| Effect class suy ra 5 hành vi | tác giả tool khai **một** thứ; không có guard viết tay per-tool | 4 lớp là thô — một tool vừa đọc vùng nhạy cảm vừa ghi không xếp gọn |
| Invariant trên đường bắt buộc, plugin cho policy | "không cài" không còn là mặc định không an toàn | plugin không làm được vài thứ (không chặn được permission check) |
| `Decision` append-only | audit thật, thu hồi bằng `max()` | sổ chỉ lớn lên; cần chính sách lưu trữ chưa viết |
| effect log luôn bật cho `write` | khuyết điểm #5 sửa **theo mặc định** | một round-trip DB thêm mỗi `write` call — **chưa benchmark** |
| At-most-once thay vì exactly-once | trung thực về cái harness một mình làm được | người dùng muốn exactly-once phải có upstream nhận key |

---

## 4. Chưa đủ evidence — hợp nhất

Từ sáu tệp thiết kế, không lặp lại lý lẽ:

- **Chi phí effect log** trên mỗi `write`. Không đo được từ source người khác.
- **`fingerprint` cho MCP stdio.** TLS SPKI pin đúng cho HTTP; không có tương đương hiển
  nhiên cho tiến trình con.
- **Biên checkpoint chính xác của LangGraph** giữa chừng một node — chưa đọc trong source.
- **Isolation đa tenant ở tầng store.** R-4 là điều kiện cần, không phải đủ.
- **Số bậc của trục confidentiality.** Hai bậc là suy luận, không phải kết quả đo.
- **TTL mặc định cho grant `danger`.** Không có bằng chứng nào về con số đúng.
- **Chính sách hết hạn memory.** Một memo `UNTRUSTED` sống mãi là rủi ro thật, nhưng nghiên
  cứu không có bằng chứng về chính sách nào đúng, nên không phát minh một chính sách.
- **Learning curve.** [§11](../research/11-workflow-and-dx.md) §45 ghi rõ nó **không được
  đo** trong nghiên cứu. Mọi tuyên bố DX ở đây dựa trên thứ đếm được (`py.typed`, số tên,
  số dòng Mức 0), không dựa trên người dùng thật.

---

## 5. Việc tiếp theo, theo thứ tự

1. **S-14 vòng đời `Reservation`** — trần chi tiêu là bất biến #4, mà ngữ nghĩa huỷ chưa có.
2. **S-6, S-7, S-10** — ba lỗ trong so khớp `Scope`, tức trong chính cơ chế được coi là điểm
   mạnh nhất học từ Microsoft.
3. **`Secret[T]`** — nguồn nâng confidentiality thứ nhất (S-3), chưa cài; chỉ có
   `Grants.sensitive` (nguồn thứ hai).
4. **Cắt K-7, K-9, K-10, K-23** — giảm đường tới production từ ~38 xuống ~33 tên.
5. **Tiếp tục viết code.** S-16/S-19/S-3 đã vào `src/harness/` — 273 test xanh, mỗi cơ
   chế chính có mutation test đi kèm. Vẫn còn 33 phát hiện review chưa chạm tới code, và
   nghiên cứu của chính dự án đo được **23 vòng review tìm 20 lỗi và 0 lỗi bảo mật; 16
   vòng chạy tìm 38+ lỗi và 4 lỗi bảo mật** — bản thiết kế là sản phẩm của review, nó sẽ
   sai ở những chỗ chỉ có chạy mới tìm ra.
