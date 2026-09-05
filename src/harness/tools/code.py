"""`CodeTools` — bộ tool cho một agent làm việc với code, đã phân loại effect và đã nhốt
trong workspace.

**Vì sao là một lớp nhận `root`, không phải một đống hàm module-level.** Một tool đụng file
mà không có gốc là một primitive rò rỉ: model tự chọn đường dẫn, nên `read_file("/etc/passwd")`
hay `read_file("../../.ssh/id_rsa")` là một câu tool_use, không phải một cuộc tấn công phức
tạp. `confine()` (T-7.1) đã có sẵn từ M7 nhưng phải có ai đó GỌI nó với một gốc cụ thể —
và một hàm module-level thì không có gốc nào để gọi cùng. Đây là cùng khuôn
`VikingStore.tools()` và `TaskLedger.tools()` đã dùng: một object giữ tài nguyên, một
`tools()` trả về các tool đã đóng gói sẵn tài nguyên đó.

**Vì sao có `search_code`/`outline` chứ không chỉ đọc-ghi.** Một agent code chỉ có
`read_file` sẽ đọc cả file để tìm một hàm — trả tiền cho toàn bộ file, mỗi lần, và lấp đầy
context bằng thứ nó không cần. `outline` trả về bản đồ cấu trúc kèm số dòng; `search_code`
trả về `file:line` khớp. Hai cái đó là phần "hiểu code" mà không có thì mọi thứ khác chỉ là
một trình soạn thảo mù.

**Vì sao `edit_source` thay thế theo chuỗi CHÍNH XÁC và từ chối khi mơ hồ.** Ghi đè cả file
(`write_source`) buộc model phải sinh lại toàn bộ nội dung — tốn token theo kích thước file
và là nguồn số một của "sửa một dòng, mất ba hàm khác". Thay thế theo chuỗi chỉ tốn theo
kích thước thay đổi. Nhưng nó chỉ an toàn khi chuỗi cũ xuất hiện ĐÚNG MỘT LẦN: khớp nhiều
chỗ nghĩa là model không thực sự biết nó đang sửa chỗ nào, nên ở đây là lỗi có hướng dẫn,
không phải "sửa đại chỗ đầu tiên".

**`run_tests`/`refresh_codebase_docs` là `danger`, không phải `write` — sửa sau một review
đối kháng (G-1, `design/review-architect.md`).** Bản nháp đầu tiên phân loại cả hai là
`write` với lý do đúng một nửa: chạy test ghi cache/artefact, không song song được. Cái nó
bỏ sót: `write_source`/`edit_source` cho phép model ghi BẤT KỲ nội dung nào vào một file
`.py`, và `run_tests` sau đó IMPORT chính file đó để chạy pytest — import một file test
CHÍNH LÀ thực thi bất kỳ code nào ở top-level của nó. Hai bước đều `write` (auto-ALLOW ở
`safety="standard"`) ghép lại thành RCE không cần duyệt: `write_source` ghi
`os.system(...)` vào `test_x.py`, `run_tests` chạy nó. Bị chứng minh bằng kịch bản thật
(`id > PWNED.txt`, chạy với quyền của tiến trình harness). `danger`'s floor là `ASK` ở cả
hai mức safety — đúng cái cần cho "thực thi code do model viết ra". `git_push`, `deploy`
vẫn là việc của người viết agent tự khai thêm bên ngoài module này.

**Vì sao `refresh_codebase_docs` là một tool tường minh, không phải một bước tự động
trước mỗi run.** OpenWiki "code mode" sinh wiki kiến trúc (`openwiki/`) với bằng chứng
gắn dòng code cụ thể — khác tài liệu viết tay, nó tự đối chiếu lại khi code đổi nên
không lỗi thời âm thầm. Nhưng chạy nó tốn một lượt gọi model RIÊNG của chính OpenWiki
(tiền, API key, thời gian) — tự động hoá trước mỗi phiên nghĩa là trả phí đó cho mọi
run kể cả khi không ai cần đọc lại wiki, trái với mọi seam khác trong harness (không gì
chạy nếu không có ai chủ động gọi tới nó). Nên đây là một tool y hệt `run_tests` hay
`git_commit`: có mặt khi cần, im lặng khi không ai gọi. `openwiki` không phải dependency
của harness (không có trong `pyproject.toml`, cùng khuôn `viking` extra của ADR-035) —
máy không cài thì tool trả lỗi đọc được, không phải một trường hợp đặc biệt.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

from ..sandbox import Subprocess
from ..workspace import confine
from . import tool

#: Cắt ở đây thay vì để một file 40 MB đi thẳng vào context. Trùng `builtin/files.py`.
MAX_BYTES: Final = 200_000
MAX_MATCHES: Final = 200
MAX_FILES: Final = 500

#: Không lục vào những chỗ này. Một `**/*` trên repo Python thật mà không loại trừ
#: `.git`/`node_modules` sẽ trả về hàng chục nghìn đường dẫn — vô dụng cho model và đắt.
SKIP_DIRS: Final = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__",
                              ".venv", "venv", ".mypy_cache", ".pytest_cache",
                              ".ruff_cache", "dist", "build", ".tox"})

#: Biến môi trường được chuyển xuống tiến trình con — DANH SÁCH CHO PHÉP, không phải
#: `os.environ`. `Sandbox.run` dùng `env` NGUYÊN VĂN (T-7.4), nên đây là toàn bộ những gì
#: lệnh con thấy: không có `ANTHROPIC_API_KEY`, không có token CI, không có gì khác.
#: `HOME` có mặt vì `git commit` cần đọc `~/.gitconfig` để biết tên người commit — bỏ nó
#: ra thì git hỏng với "Author identity unknown", đúng lỗi mà `examples/coding_agent.py`
#: đã vấp.
PASS_ENV: Final = ("PATH", "HOME", "LANG", "LC_ALL", "TZ")


def _default_env() -> dict[str, str]:
    return {k: os.environ[k] for k in PASS_ENV if k in os.environ}


def _is_probably_text(data: bytes) -> bool:
    return b"\x00" not in data[:8_000]


class CodeTools:
    """Tool code nhốt trong `root`. Mọi đường dẫn model đưa vào đều đi qua `confine()`.

    `sandbox` mặc định là `Subprocess` — tiến trình con môi trường sạch, `argv` chứ không
    phải chuỗi shell (không có gì để tiêm qua `;`/`&&`), `cwd` cố định, timeout cứng. Đây
    KHÔNG phải cô lập container (§01.5 non-goal): một lệnh cố tình vẫn thấy được filesystem
    ngoài `cwd` và mạng của host. Truyền `sandbox=` của bạn (Docker/Firecracker) vào đúng
    chỗ này khi cần thật.
    """

    def __init__(self, root: str | Path, *, sandbox: Any | None = None,
                 test_command: Sequence[str] = ("python", "-m", "pytest", "-q"),
                 timeout_s: float = 120.0,
                 env: Mapping[str, str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.sandbox = sandbox if sandbox is not None else Subprocess()
        self.test_command = tuple(test_command)
        self.timeout_s = timeout_s
        self.env = dict(env) if env is not None else _default_env()

    # ── phần không phải tool, để test và để người viết agent gọi trực tiếp ──────
    def path(self, path: str) -> Path:
        return confine(self.root, path)

    def walk(self) -> tuple[list[Path], bool]:
        """Trả về `(đường_dẫn, đã_cắt_bớt)`. `đã_cắt_bớt=True` nghĩa là workspace có
        nhiều hơn `MAX_FILES` file — người GỌI phải tự nói ra điều đó, vì im lặng cắt
        bớt ở đây từng khiến `search_code`/`list_files` báo "không tìm thấy" cho một
        chuỗi THẬT SỰ có mặt trong repo, chỉ là nằm ngoài phần đã quét (một agent code
        đọc câu đó rồi có thể viết lại một hàm đã tồn tại sẵn ở nơi khác). Cùng kỷ luật
        `search_code` đã tự áp cho `MAX_MATCHES` của chính nó — bên đó nói rõ khi cắt,
        bên này thì chưa, cho tới bản vá này."""
        out: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                out.append(Path(dirpath) / name)
                if len(out) >= MAX_FILES:
                    return out, True
        return out, False

    async def _run(self, cmd: Sequence[str]) -> str:
        done = await self.sandbox.run(list(cmd), cwd=str(self.root), env=self.env,
                                      timeout=self.timeout_s)
        head = f"exit {done.returncode}" + (" (TIMED OUT)" if done.timed_out else "")
        body = (done.stdout + done.stderr).strip()
        return f"{head}\n{body}" if body else head

    def tools(self) -> list:
        """Mười một tool, effect đã đặt sẵn — xem docstring module cho từng quyết định."""
        me = self

        @tool(effect="read")
        async def list_files(pattern: str = "*") -> str:
            """Liệt kê file trong workspace. `pattern` là glob theo tên file, ví dụ `*.py`."""
            paths, truncated = me.walk()
            names = [str(p.relative_to(me.root)) for p in paths
                     if Path(p.name).match(pattern)]
            note = (f"\n[đã dừng quét ở {MAX_FILES} file — có thể còn file khớp ngoài "
                    f"phạm vi đã quét]" if truncated else "")
            if not names:
                return f"không có file nào khớp {pattern!r}" + note
            return "\n".join(names) + note

        @tool(effect="read")
        async def read_source(path: str, start: int = 1, end: int = 0) -> str:
            """Đọc một file, kèm SỐ DÒNG. `start`/`end` giới hạn khoảng dòng (1-based,
            `end=0` nghĩa là tới hết file)."""
            p = me.path(path)
            data = p.read_bytes()[:MAX_BYTES]
            if not _is_probably_text(data):
                return f"{path} là file nhị phân, không đọc như text được"
            lines = data.decode("utf-8", "replace").splitlines()
            lo = max(1, start)
            hi = len(lines) if end <= 0 else min(len(lines), end)
            if lo > len(lines):
                return f"{path} chỉ có {len(lines)} dòng"
            # Số dòng đi kèm vì `edit_source` và mọi câu hỏi tiếp theo đều nói bằng số
            # dòng; trả về text trần buộc model tự đếm, và nó đếm sai.
            return "\n".join(f"{n:>5}| {lines[n - 1]}" for n in range(lo, hi + 1))

        @tool(effect="read")
        async def search_code(pattern: str, glob: str = "*") -> str:
            """Tìm một biểu thức chính quy trong workspace. Trả về `file:dòng: nội dung`."""
            try:
                rx = re.compile(pattern)
            except re.error as exc:
                return f"biểu thức chính quy không hợp lệ: {exc}"
            hits: list[str] = []
            paths, walk_truncated = me.walk()
            for p in paths:
                if not Path(p.name).match(glob):
                    continue
                try:
                    data = p.read_bytes()[:MAX_BYTES]
                except OSError:
                    continue
                if not _is_probably_text(data):
                    continue
                rel = p.relative_to(me.root)
                for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{n}: {line.strip()[:200]}")
                        if len(hits) >= MAX_MATCHES:
                            return "\n".join(hits) + f"\n[dừng ở {MAX_MATCHES} kết quả]"
            # `walk_truncated`: workspace có hơn MAX_FILES file, nên "không tìm thấy"
            # ở đây có thể là dương tính giả — chuỗi có thể nằm trong một file ngoài
            # phạm vi đã quét. Nói ra thay vì để model tin nhầm là chuỗi không tồn tại.
            note = (f"\n[đã dừng quét ở {MAX_FILES} file — kết quả có thể chưa đầy đủ]"
                    if walk_truncated else "")
            if not hits:
                return f"không tìm thấy {pattern!r}" + note
            return "\n".join(hits) + note

        @tool(effect="read")
        async def outline(path: str) -> str:
            """Bản đồ cấu trúc một file Python: class/def nào ở dòng nào — rẻ hơn nhiều
            so với đọc cả file chỉ để biết nó có gì."""
            p = me.path(path)
            if p.suffix != ".py":
                # Nói thật thay vì đoán: chỉ Python có parser ở đây.
                return (f"{path} không phải file Python; `outline` chỉ đọc được cấu trúc "
                        f"Python. Dùng `search_code` cho ngôn ngữ khác.")
            src = p.read_bytes()[:MAX_BYTES].decode("utf-8", "replace")
            try:
                tree = ast.parse(src)
            except SyntaxError as exc:
                return f"{path} không parse được: dòng {exc.lineno}: {exc.msg}"
            out: list[str] = []

            def walk(node: ast.AST, depth: int) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.ClassDef, ast.FunctionDef,
                                          ast.AsyncFunctionDef)):
                        kind = "class" if isinstance(child, ast.ClassDef) else "def"
                        out.append(f"{'  ' * depth}{child.lineno:>5}| {kind} {child.name}")
                        walk(child, depth + 1)

            walk(tree, 0)
            return "\n".join(out) if out else f"{path} không có class hay def nào ở mức nào"

        @tool(effect="write")
        async def write_source(path: str, text: str) -> str:
            """Ghi đè toàn bộ một file. Dùng cho file MỚI; sửa file có sẵn thì dùng
            `edit_source` — rẻ hơn và không xoá nhầm phần không định đụng."""
            p = me.path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return f"đã ghi {len(text)} ký tự vào {path}"

        @tool(effect="write")
        async def edit_source(path: str, old: str, new: str) -> str:
            """Thay chuỗi `old` bằng `new` trong một file. `old` phải xuất hiện ĐÚNG MỘT
            LẦN — kèm đủ dòng xung quanh để nó là duy nhất."""
            p = me.path(path)
            src = p.read_text(encoding="utf-8")
            n = src.count(old)
            if n == 0:
                return (f"không tìm thấy đoạn cần sửa trong {path}. Đọc lại bằng "
                        f"`read_source` rồi chép chính xác, kể cả khoảng trắng.")
            if n > 1:
                # Sửa đại chỗ đầu tiên là cách một agent code phá file mà không ai thấy
                # cho tới lúc chạy test. Từ chối kèm hướng dẫn.
                return (f"đoạn đó xuất hiện {n} lần trong {path} — không đoán chỗ nào. "
                        f"Thêm dòng phía trên hoặc phía dưới vào `old` cho đủ duy nhất.")
            p.write_text(src.replace(old, new), encoding="utf-8")
            return f"đã sửa {path} ({len(old)} ký tự → {len(new)})"

        @tool(effect="danger")
        async def run_tests(target: str = "") -> str:
            """Chạy bộ test của dự án. `target` (tuỳ chọn) giới hạn ở một file hay một test.

            Phân loại `danger`, không phải `write` (G-1, đã sửa): chạy test là IMPORT rồi
            THỰC THI bất kỳ code Python nào nằm trong file test — kể cả file model vừa tự
            ghi bằng `write_source`. `danger` là lớp duy nhất buộc duyệt trước khi chạy.

            `target` bị `confine()` (G-2, đã sửa): trước bản vá này, `target` đi thẳng vào
            argv mà không qua `me.path()` như mọi tool khác trong lớp này — một
            `target="../ngoai-workspace"` chạy test NGOÀI thư mục gốc, trên chính đường
            dẫn mà `read_source` đã từ chối đúng.
            """
            if target:
                if target.startswith("-"):
                    return (f"{target!r} bắt đầu bằng '-' — có thể bị hiểu như một cờ "
                            f"dòng lệnh của test runner, không phải một đường dẫn. Từ chối.")
                # `::test_name` (chọn một test cụ thể) là hợp lệ ở pytest — validate phần
                # đường dẫn TRƯỚC dấu `::`, không round-trip qua Path() (sẽ làm sai lệch
                # cú pháp node-id), rồi vẫn dùng `target` NGUYÊN VĂN trong argv.
                path_part = target.split("::", 1)[0]
                if path_part:
                    me.path(path_part)              # G-2: chỉ để validate containment
            cmd = list(me.test_command) + ([target] if target else [])
            return await me._run(cmd)

        @tool(effect="read")
        async def git_status() -> str:
            """Xem file nào đã đổi so với commit cuối."""
            return await me._run(["git", "status", "--porcelain=v1"])

        @tool(effect="read")
        async def git_diff(path: str = "") -> str:
            """Xem nội dung thay đổi chưa commit."""
            if path:
                if path.startswith("-"):
                    return (f"{path!r} bắt đầu bằng '-' — có thể bị hiểu như một cờ dòng "
                            f"lệnh của git, không phải một đường dẫn. Từ chối.")
                me.path(path)                       # G-2: validate containment
            return await me._run(["git", "diff"] + ([path] if path else []))

        @tool(effect="write")
        async def git_commit(message: str) -> str:
            """Commit mọi thay đổi hiện có. Hoàn tác được bằng `git reset`, nên là
            `write`; đẩy lên remote thì KHÔNG — đó là tool của bạn, `effect="danger"`."""
            add = await me._run(["git", "add", "-A"])
            if not add.startswith("exit 0"):
                return add
            return await me._run(["git", "commit", "-m", message])

        @tool(effect="danger")
        async def refresh_codebase_docs() -> str:
            """Cập nhật wiki mô tả kiến trúc repo (thư mục `openwiki/`) qua OpenWiki
            "code mode" — mỗi khẳng định gắn bằng chứng dòng code cụ thể, tự đối chiếu
            lại khi code đổi, nên KHÔNG lỗi thời âm thầm như tài liệu viết tay.

            Phân loại `danger`, không phải `write` (G-1, đã sửa): chạy một binary ngoài
            (`openwiki`) với quyền của tiến trình harness — cùng lý do `run_tests` không
            còn là `write` nữa.

            Tuỳ chọn thật sự: `openwiki` không phải dependency của harness (không có
            trong `pyproject.toml` — cùng khuôn `viking` extra, ADR-035: chạy như tiến
            trình ngoài, không vendor vào thư viện). Máy không cài `openwiki` thì tool
            này trả lỗi "not found" đọc được — như mọi lỗi khác trong file này, không
            phải một trường hợp đặc biệt phải xử lý riêng.

            Không tự động chạy — chỉ khi MODEL chủ động gọi (đúng thứ đã bàn kỹ trước
            khi thêm tool này): tự động chạy trước mỗi phiên sẽ tốn một lượt gọi model
            RIÊNG của chính OpenWiki (tiền, API key, thời gian) cho mọi run kể cả khi
            không ai cần đọc lại wiki — trái với mọi seam khác trong harness, vốn không
            gì chạy nếu không có ai gọi tới nó.

            `--init` lần đầu (chưa có `openwiki/`), `--update` các lần sau — heuristic
            dựa trên sự tồn tại của thư mục, không phải tri thức chắc chắn về hành vi
            nội bộ của CLI; sai thì `openwiki` tự báo lỗi, đọc được ngay trong kết quả
            trả về (IDL-30, fail visible) chứ không âm thầm làm sai việc.
            """
            cmd = ["openwiki", "--update" if (me.root / "openwiki").is_dir() else "--init"]
            return await me._run(cmd)

        return [list_files, read_source, search_code, outline, write_source,
                edit_source, run_tests, git_status, git_diff, git_commit,
                refresh_codebase_docs]
