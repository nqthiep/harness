"""Bộ khung một coding agent long-run, dựng trên harness — CODING_AGENT_BLUEPRINT.md.

Chạy:  python3 examples/coding_agent.py

Không cần API key: mặc định dùng model giả (kịch bản cố định, đủ để chứng minh cách các
mảnh khớp nhau). Có key thì tự dùng model thật:

    export ANTHROPIC_API_KEY=sk-ant-...
    pip install 'harness[graph]' langchain-anthropic

Năm ý chính, mỗi ý một khối chạy được:
  1. Tool coding hẹp, phân loại effect ĐÚNG — không phải một `run_shell` bao trùm tất cả.
  2. `Workspace.confine()` — tool đụng file chỉ thấy được dưới một thư mục gốc.
  3. `Sandbox` — lệnh shell chạy qua subprocess môi trường sạch, có timeout, có thể thay
     bằng Docker/Firecracker thật sau này mà không đụng gì ở trên.
  4. Ngân sách "long-run" — không phải lặp lại run(), mà MỘT invoke() với đủ step/thời
     gian để agent tự làm việc qua nhiều lượt gọi tool.
  5. Checkpointer — phiên làm việc sống qua nhiều lượt gọi `graph.invoke()`, resume được
     bằng `thread_id`.
"""
import asyncio
import os
import subprocess as _subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from harness import tool
from harness.errors import UnsafeToolSetError
from harness.sandbox import Subprocess
from harness.workspace import WorkspaceEscapeError, confine


def tieu_de(chu: str) -> None:
    print(f"\n{'═' * 70}\n{chu}\n{'═' * 70}")


# ══════════════════════════════════════════════════════════════════════════
tieu_de("1. Tool HẸP, phân loại ĐÚNG — không phải một run_shell bao trùm tất cả")
# Mỗi tool khai effect() sao cho khớp đúng những gì nó thật sự làm. Gộp mọi thứ vào một
# `run_shell(cmd: str)` duy nhất buộc harness phải phân loại nó `danger` (an toàn nhất
# cho trường hợp xấu nhất) — mọi lệnh, kể cả `git diff`, đều phải qua duyệt người. Tách
# ra theo đúng khả năng thật của từng lệnh thì phần lớn công việc coding không cần hỏi ai.

WORKSPACE = Path(tempfile.mkdtemp(prefix="coding-agent-"))
SANDBOX = Subprocess()
_subprocess.run(["git", "init", "-q"], cwd=WORKSPACE, check=True)   # chỉ để dựng demo


def _sync(coro):
    """Tool luôn `async` (IDL-06) — ví dụ này gọi trực tiếp `.fn(...)` ngoài một Agent
    thật để minh hoạ từng mảnh riêng lẻ, nên cần tự chạy loop; bên trong một Agent thật,
    harness làm việc này cho bạn."""
    return asyncio.get_event_loop().run_until_complete(coro)


@tool(effect="read")
def list_files(subdir: str = ".") -> str:
    """Liệt kê file trong một thư mục con của workspace."""
    root = confine(WORKSPACE, subdir)
    return "\n".join(sorted(p.name for p in root.iterdir())) or "(trống)"


@tool(effect="read")
def read_source(path: str) -> str:
    """Đọc nội dung một file trong workspace."""
    p = confine(WORKSPACE, path)
    return p.read_text(encoding="utf-8", errors="replace")[:200_000]


@tool(effect="write")
def write_source(path: str, text: str) -> str:
    """Ghi nội dung vào một file trong workspace, tạo mới nếu chưa có."""
    p = confine(WORKSPACE, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return f"đã ghi {len(text)} ký tự vào {path}"


async def _sh(argv: list[str], *, timeout: float = 30.0):
    """Gọi TRỰC TIẾP `await`, không tự spin loop riêng — khác `_sync()` ở trên (chỉ dùng
    ngoài Agent, để minh hoạ tool riêng lẻ). Một tool bên trong Agent chạy trong event
    loop của chính harness; tool cần I/O thật sự nên viết `async def` và `await` thẳng,
    không tự dựng loop lồng bên trong loop đang chạy — dựng lồng như vậy là lý do
    `Subprocess.run` từng "never awaited" trong bản nháp đầu của ví dụ này."""
    # `env=` được dùng NGUYÊN VĂN, không tự gộp với os.environ của tiến trình cha (T-7.4)
    # — rò rỉ biến môi trường (vd. API key của chính harness) không phải mặc định, phải
    # xin từng biến một. `HOME` cần có để `git commit` tìm được ~/.gitconfig (identity).
    return await SANDBOX.run(argv, cwd=str(WORKSPACE),
                             env={"PATH": os.environ.get("PATH", "/usr/bin"),
                                 "HOME": os.environ.get("HOME", "")},
                             timeout=timeout)


@tool(effect="read")
async def run_tests() -> str:
    """Chạy bộ test trong workspace, trả về kết quả."""
    r = await _sh(["python3", "-m", "pytest", "-q"], timeout=120.0)
    return f"returncode={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-500:]}"


@tool(effect="write")
async def git_commit(message: str) -> str:
    """Commit mọi thay đổi hiện có trong workspace — hoàn tác được (git reset)."""
    await _sh(["git", "add", "-A"])
    r = await _sh(["git", "commit", "-m", message])
    return r.stdout or r.stderr


@tool(effect="danger")
async def git_push(remote: str, branch: str) -> str:
    """Push lên một remote thật — KHÔNG dễ hoàn tác một khi ai đó đã pull."""
    r = await _sh(["git", "push", remote, branch], timeout=60.0)
    return r.stdout or r.stderr


print("  6 tool: list_files/read_source/run_tests (read), write_source/git_commit "
     "(write), git_push (danger)")
print(f"  workspace tạm: {WORKSPACE}")


# ══════════════════════════════════════════════════════════════════════════
tieu_de("2. Workspace.confine() — model không thể tự thoát khỏi thư mục gốc")
# Model có thể ĐỀ XUẤT bất kỳ đường dẫn nào trong đối số — đây là nơi harness không tin
# nó, bất kể effect gì. Không cố "escape" input (bỏ dấu .., decode rồi kiểm lại) —
# resolve tuyệt đối rồi kiểm containment đúng một lần.
(WORKSPACE / "hello.py").write_text("print('hi')\n")
print("  đọc file hợp lệ:", repr(_sync(read_source.fn(path="hello.py"))))

try:
    _sync(read_source.fn(path="../../etc/passwd"))
    print("  KHÔNG NGỜ TỚI: đọc được file ngoài workspace")
except WorkspaceEscapeError as e:
    print(f"  chặn đúng (../..): {e}")

try:
    _sync(read_source.fn(path="/etc/passwd"))
    print("  KHÔNG NGỜ TỚI: đọc được đường dẫn tuyệt đối")
except WorkspaceEscapeError as e:
    print(f"  chặn đúng (đường dẫn tuyệt đối): {e}")


# ══════════════════════════════════════════════════════════════════════════
tieu_de("3. Đọc + browse internet CÙNG một agent với git_push — bị từ chối lúc dựng")
# Đây chính là "lethal trifecta" QUICKSTART.md nói tới, áp trực tiếp vào coding agent:
# một tool external (vd. tra cứu tài liệu trên mạng) cộng một tool danger (git_push)
# trong CÙNG một agent nghĩa là một trang web độc hại có thể dụ agent push code độc.
from harness.tools.web import fetch                      # noqa: E402
from harness import Agent                                # noqa: E402

try:
    Agent(name="Bad", job="Code và tra cứu tài liệu.", tools=[fetch, git_push])
    print("  KHÔNG NGỜ TỚI: không bị chặn")
except UnsafeToolSetError as e:
    print(f"  chặn đúng lúc dựng: {str(e).splitlines()[0]}")
print("  Sửa: tách 'nghiên cứu' (đọc mạng) và 'thực thi' (git_push) thành hai agent —")
print("  hoặc accepts_tainted=['git_push'] nếu operator TỰ chịu trách nhiệm nói rõ an toàn.")


# ══════════════════════════════════════════════════════════════════════════
tieu_de("4 & 5. Ngân sách long-run + checkpointer — một phiên sống qua nhiều lượt gọi")
try:
    from langgraph.checkpoint.memory import MemorySaver
    from langchain_core.messages import HumanMessage
    from harness.lg import build_agent
except ImportError:
    print("  cần `pip install 'harness[graph]'` — bỏ qua phần này")
else:
    def lay_model(kich_ban):
        if os.environ.get("ANTHROPIC_API_KEY"):
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model="claude-opus-5")
        from fake_chat import FakeChat
        return FakeChat(script=kich_ban)

    from fake_chat import FakeChat

    def duyet_git_push(call, ctx) -> bool:
        print(f"    [cần người duyệt] {call.name}({call.arguments}) -> mặc định TỪ CHỐI trong ví dụ này")
        return False

    graph, _rt = build_agent(
        model=lay_model([FakeChat.call("write_source", {"path": "hello.py",
                                                        "text": "print('hi v2')\n"}, "c1"),
                         FakeChat.call("run_tests", {}, "c2"),
                         FakeChat.call("git_commit", {"message": "cap nhat hello.py"}, "c3"),
                         FakeChat.text("Đã sửa, test đã chạy, đã commit.")]),
        tools=[list_files, read_source, write_source, run_tests, git_commit],
        # "Long-run" ở đây không phải nhiều lần gọi run() — mà MỘT invoke() được phép đủ
        # step/thời gian để agent tự lặp (sửa file -> chạy test -> đọc lỗi -> sửa lại...)
        # cho tới khi xong hoặc hết ngân sách. Mặc định (20 step, 5 phút) hợp cho một câu
        # hỏi; một task coding cần nhiều hơn hẳn.
        budget="$5, 300 steps, 45m",
        approve=duyet_git_push,
        checkpointer=MemorySaver(),          # thật thì SqliteSaver/PostgresSaver — sống
                                             # qua cả việc tắt tiến trình, không chỉ qua
                                             # nhiều lượt invoke() trong CÙNG tiến trình
    )

    cau_hinh = {"configurable": {"thread_id": "task-42"}}
    ket_qua = graph.invoke({"messages": [HumanMessage("Sửa hello.py để in 'hi v2', chạy "
                                                       "test, rồi commit.")]}, cau_hinh)
    print(f"  {ket_qua['messages'][-1].content}")

    trang_thai = graph.get_state(cau_hinh).values
    print(f"  checkpoint: {trang_thai['step']} bước, đã tiêu ${trang_thai['spent_usd']}")
    print("  cùng thread_id 'task-42' gọi invoke() lần nữa (kể cả sau khi tiến trình")
    print("  này tắt, nếu checkpointer là SqliteSaver) sẽ tiếp tục đúng phiên này.")


# ══════════════════════════════════════════════════════════════════════════
tieu_de("Đánh giá — Trajectory contract, không phải đọc log bằng mắt")
from harness.testing import Trajectory, FakeModel                        # noqa: E402
from harness import Agent as ClassicAgent                                # noqa: E402

danh_gia = ClassicAgent(
    name="Coder", job="Sửa lỗi và commit.",
    tools=[write_source, run_tests, git_commit],
    budget="$5, 300 steps, 45m",
    provider=FakeModel([FakeModel.tool_call("write_source",
                                            {"path": "hello.py", "text": "print('hi v3')"}),
                        FakeModel.tool_call("run_tests", {}),
                        FakeModel.tool_call("git_commit", {"message": "fix"}),
                        FakeModel.text("Xong.")]))
ket_qua = danh_gia.try_run("Sửa hello.py rồi commit.")
report = Trajectory(
    must_call=frozenset({"write_source", "git_commit"}),
    must_not_call=frozenset({"git_push"}),          # task này không được phép push
    no_duplicate_side_effects=True,
).check(ket_qua, effect_of={"write_source": "write", "git_commit": "write",
                            "git_push": "danger", "run_tests": "read"})
print(f"  trajectory.ok = {report.ok}  (tools_run = {ket_qua.tools_run})")

print(f"""
{'─' * 70}
Tóm tắt kiến trúc — xem CODING_AGENT_BLUEPRINT.md cho đầy đủ.
""")
