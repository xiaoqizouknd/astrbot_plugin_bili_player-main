"""Tiny dependency doubles for protocol modules in the local unit-test runtime."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types


def _register_plugin_package() -> None:
    """Expose the checkout directory as the ``astrbot_plugin_bili_player`` package.

    The plugin directory usually carries a ``-main``/``-master`` suffix which
    is not a valid module name, so plain ``sys.path`` insertion cannot make
    ``astrbot_plugin_bili_player.main`` importable.  Registering the package
    explicitly mirrors what AstrBot's plugin loader does in production.
    """
    name = "astrbot_plugin_bili_player"
    if name in sys.modules:
        return
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        name,
        root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法注册插件包 {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


def ensure_aiohttp() -> None:
    """Install just enough of aiohttp's public shape when AstrBot is not installed."""
    try:
        import aiohttp  # noqa: F401
    except ModuleNotFoundError:
        module = types.ModuleType("aiohttp")

        class ClientError(Exception):
            pass

        class ClientSession:
            pass

        class ClientResponse:
            pass

        module.ClientError = ClientError
        module.ClientSession = ClientSession
        module.ClientResponse = ClientResponse
        sys.modules["aiohttp"] = module
