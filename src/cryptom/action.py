import inspect
import os
from typing import Any, Dict

from loguru import logger

try:
    from imp import load_source
except ImportError:
    from importlib.util import module_from_spec, spec_from_file_location

    def load_source(name, path):
        s = spec_from_file_location(name, path)
        if s is None:
            raise ImportError(f"Cannot find module {name} at {path}")
        m = module_from_spec(s)
        if s.loader is None:
            raise ImportError(f"Cannot load module {name} at {path}")
        s.loader.exec_module(m)
        return m


class register:
    def __init__(self, func):
        self.func = func
        self.func_args_names = inspect.getfullargspec(func).args
        self.func.__globals__["cryptom_action_handler"] = self
        self.func.__globals__["print"] = logger.info

    def action(self, exchange, context):
        available_content = {
            "exchange": exchange,
            "context": context,
        }
        kwargs = {}
        for name in self.func_args_names:
            if name in available_content:
                kwargs[name] = available_content[name]
            else:
                logger.warning(
                    f"Unsupport argument {name}, please check your function definition."
                )
        try:
            self.func(**kwargs)
        except TypeError as e:
            logger.error(f"Function {self.func.__name__} execution error: {e}")


class ActionCache:
    """脚本模块缓存管理器"""

    def __init__(self):
        # 结构: { "path": {"mtime": float, "module": module_obj} }
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get_module(self, path: str):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Script not found: {path}")

        current_mtime = os.path.getmtime(path)

        # 检查缓存是否存在且未过期
        if path in self._cache:
            cached = self._cache[path]
            if cached["mtime"] == current_mtime:
                return cached["module"]
            else:
                logger.info(f"Script changed, reloading: {path}")

        # 加载模块
        # 使用文件名作为模块名，防止所有插件都叫 cryptom_action_module 导致 sys.modules 冲突
        module_name = f"cryptom_action_{hash(path)}"
        module = load_source(module_name, path)

        # 更新缓存
        self._cache[path] = {"mtime": current_mtime, "module": module}
        return module


# 全局单例缓存
_script_cache = ActionCache()


def runAction(path, exchange, context):
    module = _script_cache.get_module(path)
    if not hasattr(module, "cryptom_action_handler"):
        raise RuntimeWarning(f"No action function found in {path}")
    if not isinstance(module.cryptom_action_handler, register):
        raise RuntimeWarning(f"action is not registered properly in {path}")
    handler: register = module.cryptom_action_handler
    handler.action(exchange, context)
