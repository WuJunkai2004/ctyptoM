import asyncio
from datetime import datetime
from time import time
from typing import Any, Optional

import ccxt as ccxt
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from .config import AppConfig, TaskConfig


class TaskEngine:
    def __init__(self, config: TaskConfig):
        self.config = config
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

    async def init(self, engine: "CryptoEngine"):
        self.engine = engine
        if not self.config.exchange:
            logger.warning(
                f"Task {self.name} has no exchange specified; skipping function binding"
            )
            return
        exchange_instance = engine.get_exchange(self.config.exchange)
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
                    args.append(eval(expr))
                except Exception:
                    args.append(expr)
            else:
                args.append(expr)

        kwargs = {}
        for k, v in self.kwargs.items():
            if isinstance(v, str):
                try:
                    kwargs[k] = eval(v)
                except Exception:
                    kwargs[k] = v
            else:
                kwargs[k] = v

        return args, kwargs

    async def execute(self):
        logger.info(f"Executing task: {self.name}")
        # init context for eval
        context = {self.name: None}

        # load dependencies
        for dep in self.dependencies:
            context[dep] = await self.engine.get_data(dep)

        result = None
        if self.function_obj:
            args, kwargs = self._prepare_params()
            if asyncio.iscoroutinefunction(self.function_obj):
                result = await self.function_obj(*args, **kwargs)
            else:
                result = self.function_obj(*args, **kwargs)
        context[self.name] = result

        if self.return_expr:
            try:
                result = eval(self.return_expr, {}, context)
            except Exception as e:
                logger.error(f"Task {self.name} return_expr evaluation error: {e}")
                return
        context[self.name] = result

        self.engine.update_data(self.name, result)

        if self.condition:
            try:
                if not eval(self.condition, {}, context):
                    return
                if self.log:
                    try:
                        logger.info("\n" + eval(f"f{repr(self.log)}", {}, context))
                    except Exception as e:
                        logger.error(f"Task {self.name} log format error: {e}")
                if self.script:
                    # @TODO: 执行外部脚本
                    logger.info(f"Triggering action: {self.script}")
            except Exception as e:
                logger.error(f"Task {self.name} condition check error: {e}")


class DataCache:
    def __init__(self):
        self.data = None
        self.update_time = None

    def update(self, data: Any):
        self.data = data
        self.update_time = time()

    def get(self) -> Optional[Any]:
        if self.update_time and time() - self.update_time < 5:
            return self.data
        else:
            return None

    def get_force(self) -> Any:
        return self.data


class CryptoEngine:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.exchanges: dict[str, ccxt.Exchange] = {}
        self.tasks: dict[str, TaskEngine] = {}
        self.data_store: dict[str, DataCache] = {}  # 存储任务结果

    async def init(self):
        """初始化所有资源"""
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
                # 构造配置字典
                conf = {
                    "apiKey": ex_config.apiKey,
                    "secret": ex_config.secret,
                    "password": ex_config.password,
                    "enableRateLimit": ex_config.enableRateLimit,
                    "options": ex_config.options,
                }
                # 过滤掉 None 值
                conf = {k: v for k, v in conf.items() if v is not None}

                exchange = exchange_class(conf)
                self.exchanges[ex_config.name] = exchange
                logger.info(f"Exchange initialized: {ex_config.name}")
            except Exception as e:
                logger.error(f"Failed to initialize exchange {ex_config.name}: {e}")

    async def _init_tasks(self):
        for task_conf in self.config.tasks:
            task_engine = TaskEngine(task_conf)
            await task_engine.init(self)
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

    def get_exchange(self, name: str) -> Optional[ccxt.Exchange]:
        return self.exchanges.get(name)

    async def get_data(self, key: str) -> Any:
        cache_obj = self.data_store.get(key, DataCache())
        cache = cache_obj.get()
        if cache is not None:
            return cache
        task = self.tasks.get(key)
        if not task:
            logger.error(f"Data for key {key} not found and no associated task")
            return None
        # if task is running, maybe it is in a deadlock, so we cannot execute it again
        # just get the data from cache, Do not care the result
        if task is asyncio.current_task():
            logger.warning(f"Task {key} is already running; returning cached data")
            return cache_obj.get_force()
        await task.execute()
        return self.data_store.get(key, DataCache()).get()

    def update_data(self, key: str, value: Any):
        if key not in self.data_store:
            self.data_store[key] = DataCache()
        self.data_store[key].update(value)

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
