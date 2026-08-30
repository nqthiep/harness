#!/usr/bin/env python3
"""Bộ đo dùng cho toàn bộ nghiên cứu — công khai để mọi con số tái lập được.

    python3 research/harvest.py <thư-mục-chứa-các-gói-đã-giải-nén>

Một agent phụ đọc bản nháp đầu tiên đã chỉ ra rằng các regex sinh ra bảng mật độ
không được ghi ở đâu cả. Nhận xét đó đúng: một bảng số không tái lập được thì
không phải bằng chứng, nó là lời khẳng định. Tệp này là bộ đo đó.

GIỚI HẠN, nói trước vì nó quyết định cách đọc bảng:
  1. Đếm đo SỰ HIỆN DIỆN, không đo TÍNH ĐÚNG. Một gói có `retry` khắp nơi vẫn có
     thể retry sai.
  2. Đếm gồm cả comment, docstring và test — chúng là bằng chứng về mối bận tâm
     của tác giả, nhưng không phải bằng chứng về hành vi lúc chạy.
  3. Chuẩn hoá theo kLOC để so sánh gói 13 kLOC với gói 172 kLOC. Không chuẩn hoá
     thì bảng chỉ đo kích thước.
  4. Mọi con số nổi bật phải được XÁC MINH bằng cách đọc code thật trước khi kết
     luận. Trong nghiên cứu này, chính bước đó đã lật ngược ý nghĩa của
     `idempoten*`: grep thì có, đọc vào thì hầu hết là no-op nội bộ hoặc HTTP header.
  5. HEADER GIẤY PHÉP KHÔNG PHẢI LÀ CODE. Câu Apache-2.0 "See the License for the
     specific language governing PERMISSIONS and limitations" khớp probe
     `permission`. Java/Kotlin/C#/Go đặt header này vào MỌI file, nên probe
     `permission` của một project Apache là gần như toàn nhiễu: spring-ai đo được
     11,8/kLOC, đọc vào chỉ còn 0,03 — sai 390 lần. Trong Python chỉ google-adk
     theo quy ước này, và số `permission` của nó đã từng bị công bố sai
     (5,3 → 1,3; xem đính chính trong 00-executive-summary.md).
     `_LICENSE_NOISE` dưới đây loại các dòng đó trước khi đếm.
"""
from __future__ import annotations

import pathlib
import re
import sys

#: Bộ probe. Giữ nguyên giữa các lần chạy, nếu không thì các bảng không ghép được.
PROBES: dict[str, str] = {
    "sandbox":    r"\b(sandbox|E2BExecutor|DockerExecutor|seccomp|container)\b",
    "approval":   r"\b(approval|require_approval|human_in_the_loop|interrupt\(|needs_approval)\b",
    "permission": r"\b(permission|allowlist|allow_list|denylist|authoriz)\w*",
    "budget":     r"\b(max_turns|max_steps|token_budget|budget|max_iterations|usage_limit)\w*",
    "retry":      r"\b(retry|retries|backoff)\b",
    "idempot":    r"\bidempoten\w*",
    "checkpt":    r"\b(checkpoint|resume)\b",
    "cancel":     r"\b(cancel|CancelledError)\w*",
    "otel":       r"\b(opentelemetry|otel|tracer|span)\b",
    "cost":       r"\b(cost|pricing|usd)\b",
    "mcp":        r"\bmcp\b",
}


#: Dòng header giấy phép — loại trước khi đếm. Xem giới hạn (5) ở trên.
_LICENSE_NOISE = re.compile(
    r"governing permissions|under the License|Licensed under|"
    r"WITHOUT WARRANTIES|Apache License|distributed under",
    re.I,
)

#: Đuôi file được đọc, theo ngôn ngữ.
SUFFIXES = ("*.py", "*.ts", "*.java", "*.kt", "*.cs", "*.go")


def strip_license(text: str) -> str:
    """Bỏ các dòng thuộc header giấy phép.

    Lọc theo DÒNG chứ không theo file: một file vẫn có thể vừa có header vừa có
    code thật, và ta chỉ muốn mất phần header.
    """
    return "\n".join(ln for ln in text.splitlines() if not _LICENSE_NOISE.search(ln))


def do(root: pathlib.Path) -> dict[str, tuple[int, dict[str, float]]]:
    out = {}
    for pkg in sorted(p for p in root.iterdir() if p.is_dir()):
        parts = []
        for suffix in SUFFIXES:
            parts += [f.read_text(errors="ignore") for f in pkg.rglob(suffix)
                      if f.stat().st_size < 2_000_000]
        text = strip_license("\n".join(parts))
        kloc = max(1, text.count("\n") // 1000)
        out[pkg.name] = (kloc, {k: len(re.findall(v, text, re.I)) / kloc
                                for k, v in PROBES.items()})
    return out


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    res = do(root)
    if not res:
        print(f"không thấy gói nào trong {root}")
        return 1
    names = list(res)
    print(f"{'kLOC':<12}" + "".join(f"{n[:11]:>13}" for n in names))
    print(f"{'':<12}" + "".join(f"{res[n][0]:>13}" for n in names))
    print("─" * (12 + 13 * len(names)))
    for probe in PROBES:
        print(f"{probe:<12}" + "".join(f"{res[n][1][probe]:>13.1f}" for n in names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
