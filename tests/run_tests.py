#!/usr/bin/env python3
"""轻量测试运行器：无 pytest 依赖。

pytest 就绪后直接用 pytest 跑；本脚本用于受限环境（只需标准库 + 项目依赖）。
提供 pytest.approx / monkeypatch 的最小兼容实现，收集并运行 test_* 函数。
"""
import math
import sys
import types
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---- pytest 最小 shim ----
class _Approx:
    def __init__(self, val, rel=None, abs_=None):
        self.val = val
        self.rel = 1e-6 if rel is None else rel
        self.abs = 1e-12 if abs_ is None else abs_

    def __eq__(self, other):
        try:
            return abs(other - self.val) <= max(self.rel * abs(self.val), self.abs)
        except TypeError:
            return NotImplemented

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return f"approx({self.val!r}, rel={self.rel})"


class _ApproxScalar(float):
    """支持 pytest.approx(x) == y 与 y == pytest.approx(x) 双向比较。"""
    def __new__(cls, val, rel=None):
        return super().__new__(cls, val)


def _approx(val, rel=None, abs=None):
    return _Approx(val, rel, abs)


class _MonkeyPatch:
    def __init__(self):
        self._saved = []

    def setattr(self, target, name, value):
        old = getattr(target, name)
        self._saved.append((target, name, old))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._saved):
            setattr(target, name, old)
        self._saved.clear()


def _fixture(func):
    func.__is_fixture__ = True
    return func


fake_pytest = types.ModuleType("pytest")
fake_pytest.approx = _approx
fake_pytest.fixture = lambda *a, **k: (a[0] if a else _fixture)
sys.modules.setdefault("pytest", fake_pytest)


# ---- 运行 ----
FIXTURES = {"monkeypatch": lambda: _MonkeyPatch()}
_results = []


def _inspect_spec(func) -> list:
    import inspect
    return [p for p in inspect.signature(func).parameters
            if p not in ("self", "cls")]


def main() -> int:
    test_files = sorted(Path(__file__).parent.glob("test_*.py"))
    passed = failed = 0
    for tf in test_files:
        if tf.name == Path(__file__).name:
            continue
        mod_name = f"tests.{tf.stem}"
        try:
            mod = __import__(mod_name, fromlist=["*"])
        except Exception:
            print(f"❌ {tf.name}: import 失败")
            traceback.print_exc()
            failed += 1
            continue
        for name in sorted(dir(mod)):
            if not name.startswith("test_") or name == "test_all":
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            args = []
            ctx = None
            bad_fixture = False
            for p in _inspect_spec(fn):
                if p in FIXTURES:
                    ctx = FIXTURES[p]()
                    args.append(ctx)
                else:
                    bad_fixture = True
                    break
            if bad_fixture:
                continue
            try:
                fn(*args)
                passed += 1
                print(f"  ✅ {tf.name}::{name}")
            except Exception:
                failed += 1
                print(f"  ❌ {tf.name}::{name}")
                for line in traceback.format_exc().splitlines()[-6:]:
                    print("       " + line)
            finally:
                if ctx is not None:
                    ctx.undo()
    print(f"\n通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
