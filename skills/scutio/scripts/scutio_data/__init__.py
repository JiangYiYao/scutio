"""Scutio 取数包。

公开调用使用域模块，例如 ``from scutio_data.market import security_quote``。
包根仅暴露域命名空间与路径引导函数，不再把底层 Session、单源实现和测试钩子
平铺成数百个符号。"""

from importlib import import_module

from scutio_data.paths import (
    default_python_path,
    default_scutio_home,
    ensure_on_syspath,
    ensure_toolkit_for_skill_script,
    find_scripts_dir,
    scripts_dir_candidates,
)

_DOMAIN_MODULES = frozenset(
    (
        "announcements",
        "breadth",
        "capital",
        "events",
        "feeds",
        "fundamentals",
        "macro",
        "market",
        "paths",
        "research",
        "valuation",
    )
)


def __getattr__(name):
    if name in _DOMAIN_MODULES:
        module = import_module("%s.%s" % (__name__, name))
        globals()[name] = module
        return module
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


__all__ = [
    "announcements",
    "breadth",
    "capital",
    "events",
    "feeds",
    "fundamentals",
    "macro",
    "market",
    "paths",
    "research",
    "valuation",
    "default_python_path",
    "default_scutio_home",
    "ensure_on_syspath",
    "ensure_toolkit_for_skill_script",
    "find_scripts_dir",
    "scripts_dir_candidates",
]
