import asyncio
import inspect
from datetime import datetime
from pathlib import Path
from time import time

import ccxt.async_support as ccxt
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from .action import runAction
from .config import AppConfig, TaskConfig
from .stored import initDatabase, saveTaskResult


def _eval(expr, ctx=None):
    return eval(expr, {}, ctx or {})


class TaskEngine:
    def __init__(self, config: TaskConfig, engine: "CryptoEngine"):
        self.config = config
        self.engine = engine
        self.name = config.name

        self.function_name = config.function
        self.function_obj = None
        self.args = config.args or []
        self.kwargs = config.kwargs or {}

        self.dependencies = config.dependencies or []
        self.interval = config.interval

        self.return_expr = config.return_expr
        self.condition = (
            config.condition or "True"
        )  # if condition isn't exists, always log and execute action
        self.log = config.log
        self.script = config.action

        self._is_executed: bool = False
        self._cache_value: object = None
        self._cache_time: float = 0.0
        self._lock = asyncio.Lock()
        self._is_running: bool = False
        self._ttl: int = getattr(config, "interval", 0) or 5

        self.synoposis = {
            "exchange": config.exchange,
            "symbol": self.args[0] if len(self.args) else "unknown",
            "function": self.function_name,
        }

    @property
    def is_cache_valid(self):
        if self._cache_value is None:
            return False
        return (time() - self._cache_time) < self._ttl

    async def get_result(self):
        if self.is_cache_valid:
            return self._cache_value

        if self._is_running and self._is_executed:
            return self._cache_value

        async with self._lock:
            if self.is_cache_valid:
                return self._cache_value
            await self._locked_execute()
            return self._cache_value

    async def init(self):
        if not self.config.exchange:
            logger.warning(
                f"Task {self.name} has no exchange specified; skipping function binding"
            )
            return
        exchange_instance = self.engine.get_exchange(self.config.exchange)
        if exchange_instance and self.function_name:
            self.function_obj = getattr(exchange_instance, self.function_name, None)
            if self.function_obj:
                logger.info(
                    f"Task {self.name} bound to function {self.function_name} of exchange {self.config.exchange}"
                )
            else:
                logger.error(
                    f"Function {self.function_name} not found in exchange {self.config.exchange}"
                )
        elif not exchange_instance:
            logger.error(
                f"Exchange {self.config.exchange} not found or failed to initialize"
            )

    def _prepare_params(self) -> tuple[list, dict]:
        args = []
        for expr in self.args:
            if isinstance(expr, str):
                try:
                    args.append(_eval(expr))
                except Exception:
                    args.append(expr)
            else:
                args.append(expr)

        kwargs = {}
        for k, v in self.kwargs.items():
            if isinstance(v, str):
                try:
                    kwargs[k] = _eval(v)
                except Exception:
                    kwargs[k] = v
            else:
                kwargs[k] = v

        return args, kwargs

    async def execute(self):
        if self._is_running:
            logger.info(f"Task {self.name} skipped (already running).")
            return
        async with self._lock:
            await self._locked_execute()

    async def _locked_execute(self):
        self._is_running = True
        try:
            await self._core_execute()
        except ccxt.ExchangeNotAvailable:
            logger.error(
                f"Exchange not available for task {self.name} with exchange {self.config.exchange}"
            )
        finally:
            self._is_executed = True
            self._is_running = False

    async def _core_execute(self):
        logger.info(f"Executing task: {self.name}")
        # init context for eval
        context = {"last": self._cache_value}

        # load dependencies
        for dep in self.dependencies:
            context[dep] = await self.engine.get_data(dep)

        # execute function to fetch result
        result = None
        if self.function_obj:
            args, kwargs = self._prepare_params()
            if asyncio.iscoroutinefunction(self.function_obj):
                result = await self.function_obj(*args, **kwargs)
            else:
                result = self.function_obj(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
        context[self.name] = result
        context["this"] = result

        # if return_expr exists, re-evaluate result
        if self.return_expr:
            try:
                result = _eval(self.return_expr, context)
                if type(result) is str:
                    result = _eval(result)
            except Exception as e:
                logger.error(f"Task {self.name} return_expr _evaluation error: {e}")
                return
        context[self.name] = result
        context["this"] = result

        self._cache_time = time()
        self._cache_value = result
        saveTaskResult(self.name, result, self._cache_time, self.synoposis)

        try:
            if not _eval(self.condition, context):
                return
            if self.log:
                try:
                    logger.info("\n" + _eval(f"f{repr(self.log)}", context))
                except Exception as e:
                    logger.error(f"Task {self.name} log format error: {e}")
            if self.script:
                script = self.script.strip()
                if not script.lower().endswith(".py"):
                    script += ".py"
                script_path = Path(script)
                if not script_path.is_absolute():
                    script_path = Path(".") / script_path
                current_exchange = None
                if self.config.exchange:
                    current_exchange = self.engine.get_exchange(self.config.exchange)
                await asyncio.to_thread(
                    runAction, str(script_path), current_exchange, context
                )
        except Exception as e:
            logger.error(f"Task {self.name} condition check error: {e}")


class CryptoEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.exchanges: dict[str, ccxt.Exchange] = {}
        self.tasks: dict[str, TaskEngine] = {}

    async def init(self):
        """初始化所有资源"""
        initDatabase(self.config.database)
        await self._init_exchanges()
        await self._init_tasks()

    async def _init_exchanges(self):
        for ex_config in self.config.exchanges:
            try:
                # 动态获取 ccxt 类
                exchange_id = ex_config.name.lower()
                if not hasattr(ccxt, exchange_id):
                    logger.error(f"Exchange {exchange_id} not supported by CCXT")
                    continue

                exchange_class = getattr(ccxt, exchange_id)
                conf = ex_config.model_dump(exclude={"name"}, exclude_none=True)
                exchange = exchange_class(conf)
                self.exchanges[ex_config.name] = exchange
                logger.info(f"Exchange initialized: {ex_config.name}")
            except Exception as e:
                logger.error(f"Failed to initialize exchange {ex_config.name}: {e}")

    async def _init_tasks(self):
        self.scheduler = AsyncIOScheduler()
        for task_conf in self.config.tasks:
            task_engine = TaskEngine(task_conf, self)
            await task_engine.init()
            self.tasks[task_conf.name] = task_engine

            if task_conf.interval:
                self.scheduler.add_job(
                    task_engine.execute,
                    "interval",
                    seconds=task_conf.interval,
                    id=task_conf.name,
                    replace_existing=True,
                    next_run_time=datetime.now(),  # 立即执行
                )
                logger.info(
                    f"Task scheduled: {task_conf.name} (every {task_conf.interval}s)"
                )

    def get_exchange(self, name: str) -> ccxt.Exchange | None:
        return self.exchanges.get(name)

    async def get_data(self, key: str) -> object:
        task = self.tasks.get(key)
        if not task:
            logger.error(f"Task {key} not found")
            return None
        return await task.get_result()

    async def start(self):
        logger.info("Starting CryptoEngine...")
        await self.init()
        self.scheduler.start()

    async def stop(self):
        logger.info("Stopping CryptoEngine...")
        if self.scheduler.running:
            self.scheduler.shutdown()

        for name, exchange in self.exchanges.items():
            try:
                close_func = getattr(exchange, "close", None)
                if close_func and asyncio.iscoroutinefunction(close_func):
                    await close_func()
                logger.info(f"Exchange closed: {name}")
            except Exception as e:
                logger.error(f"Error closing exchange {name}: {e}")
