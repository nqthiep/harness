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

**Không có tool `danger` nào trong module này.** `git_push`, `deploy`, `rm -rf` là việc của
người viết agent tự khai, với đầy đủ ý thức về những gì `effect="danger"` kéo theo (duyệt
tay, không chạy song song, và luật lethal-trifecta lúc dựng agent). Nhập module này không
bao giờ tự nó tạo ra bộ ba chết người.
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

    def walk(self) -> list[Path]:
        out: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                out.append(Path(dirpath) / name)
                if len(out) >= MAX_FILES:
                    return out
        return out

    async def _run(self, cmd: Sequence[str]) -> str:
        done = await self.sandbox.run(list(cmd), cwd=str(self.root), env=self.env,
                                      timeout=self.timeout_s)
        head = f"exit {done.returncode}" + (" (TIMED OUT)" if done.timed_out else "")
        body = (done.stdout + done.stderr).strip()
        return f"{head}\n{body}" if body else head

    def tools(self) -> list:
        """Bảy tool, effect đã đặt sẵn — xem docstring module cho từng quyết định."""
        me = self

        @tool(effect="read")
        async def list_files(pattern: str = "*") -> str:
            """Liệt kê file trong workspace. `pattern` là glob theo tên file, ví dụ `*.py`."""
            names = [str(p.relative_to(me.root)) for p in me.walk()
                     if Path(p.name).match(pattern)]
            if not names:
                return f"không có file nào khớp {pattern!r}"
            return "\n".join(names)

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
            for p in me.walk():
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
            return "\n".join(hits) if hits else f"không tìm thấy {pattern!r}"

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

        @tool(effect="write")
        async def run_tests(target: str = "") -> str:
            """Chạy bộ test của dự án. `target` (tuỳ chọn) giới hạn ở một file hay một test.

            Phân loại `write` chứ không phải `read`: một lượt chạy test ghi cache, sinh
            artefact, và hai lượt chạy song song giẫm lên nhau — `write` là lớp duy nhất
            nói đúng cả ba điều đó (không song song, không tự retry).
            """
            cmd = list(me.test_command) + ([target] if target else [])
            return await me._run(cmd)

        @tool(effect="read")
        async def git_status() -> str:
            """Xem file nào đã đổi so với commit cuối."""
            return await me._run(["git", "status", "--porcelain=v1"])

        @tool(effect="read")
        async def git_diff(path: str = "") -> str:
            """Xem nội dung thay đổi chưa commit."""
            return await me._run(["git", "diff"] + ([path] if path else []))

        @tool(effect="write")
        async def git_commit(message: str) -> str:
            """Commit mọi thay đổi hiện có. Hoàn tác được bằng `git reset`, nên là
            `write`; đẩy lên remote thì KHÔNG — đó là tool của bạn, `effect="danger"`."""
            add = await me._run(["git", "add", "-A"])
            if not add.startswith("exit 0"):
                return add
            return await me._run(["git", "commit", "-m", message])

        return [list_files, read_source, search_code, outline, write_source,
                edit_source, run_tests, git_status, git_diff, git_commit]
