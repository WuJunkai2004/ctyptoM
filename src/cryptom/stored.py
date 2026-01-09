import json
from datetime import datetime
from typing import Any, Optional

from loguru import logger
from peewee import (
    CharField,
    Database,
    DateTimeField,
    FloatField,
    ForeignKeyField,
    Model,
    MySQLDatabase,
    PostgresqlDatabase,
    Proxy,
    SqliteDatabase,
    TextField,
)

from .config import DatabaseConfig

# 使用 Proxy 实现延迟初始化
db_proxy = Proxy()

# name -> task 的上下文缓存，用于存储额外信息，供 saveTaskResult 使用
matchCache: dict[str, "TaskMeta"] = {}


class BaseModel(Model):
    class Meta:
        database = db_proxy


class TaskMeta(BaseModel):
    """
    任务元数据表
    存储任务的静态定义，避免数据表冗余。
    """

    # 任务唯一名称
    name = CharField(unique=True, index=True, primary_key=True)
    # 以下字段用于 AI 助手理解数据的上下文
    # 例如：name="fetch_btc_okx", exchange="okx", symbol="BTC/USDT"
    exchange = CharField(null=True)
    symbol = CharField(null=True)
    # function name
    task_type = CharField(null=True, help_text="function name, e.g., fetch_ticker")
    # 记录创建时间
    created_at = DateTimeField(default=datetime.now)


class TaskRecord(BaseModel):
    """
    任务执行记录表
    存储高频时序数据。
    """

    # 外键关联 TaskMeta，删除任务定义时级联删除数据（可选）
    task = ForeignKeyField(TaskMeta, backref="records", on_delete="CASCADE")
    # 核心时序字段
    timestamp = DateTimeField(index=True, default=datetime.now)
    # value_num: 存储提取出的数值结果。
    # 用途：TradingView 画图、阈值告警、计算价差。
    # 来源：如果是 ticker，存 last price；如果是 spread，存计算结果。
    value_num = FloatField(null=True)
    # value_raw: 存储完整的 JSON 结果。
    # 用途：AI 助手分析。
    # 来源：ccxt 返回的完整 ticker 字典，或者 action 的完整上下文。
    # SQLite 没有原生 JSON 类型，使用 Text 存 JSON 字符串
    value_raw = TextField(null=True)

    class Meta:  # type: ignore
        indexes = ((("task", "timestamp"), False),)


def databaseBuilder(config: DatabaseConfig) -> Optional[Database]:
    if config.provider.lower() == "sqlite":
        return SqliteDatabase(
            config.database,
            pragmas={
                "journal_mode": "wal",  # 开启 Write-Ahead Logging，支持高并发读写
                "cache_size": -1024 * 64,  # 64MB 缓存
            },
        )
    elif config.provider.lower() == "postgresql":
        return PostgresqlDatabase(
            config.database,
            user=config.user,
            password=config.password,
            host=config.host,
            port=config.port,
        )
    elif config.provider.lower() == "mysql":
        return MySQLDatabase(
            config.database,
            user=config.user,
            password=config.password,
            host=config.host,
            port=config.port,
        )
    else:
        logger.error(f"Unsupported database provider: {config.provider}")
        return None


def initDatabase(config: DatabaseConfig):
    """根据配置初始化数据库连接"""
    database = databaseBuilder(config)
    if database is None:
        return
    # 绑定代理
    db_proxy.initialize(database)
    # 自动建表 (safe=True 会忽略已存在的表)
    database.connect()
    database.create_tables([TaskMeta, TaskRecord], safe=True)
    logger.info(f"Database initialized: {config.provider} -> {config.database}")


def saveTaskResult(
    task_name: str, result: Any, store_time: float, context: Optional[dict] = None
):
    """
    通用的数据保存函数，供 Engine 调用
    """
    try:
        if task_name not in matchCache:
            if context is None:
                context = {}
            defaults = {
                "exchange": context.get("exchange") or "unknown",
                "symbol": context.get("symbol") or "unknown",
                "task_type": context.get("function") or "unknown",
            }
            task_meta, created = TaskMeta.get_or_create(
                name=task_name, defaults=defaults
            )
            matchCache[task_name] = task_meta
        else:
            task_meta = matchCache[task_name]

        # 2. 处理数据
        val_num = None
        val_raw = None

        # 尝试提取数值用于画图
        if isinstance(result, (int, float)):
            val_num = result
            val_raw = str(result)
        elif isinstance(result, dict):
            # 如果是字典，尝试提取核心数值 (ccxt 标准字段)
            # 优先顺序: last -> price -> close -> value
            for key in ["last", "price", "close", "value"]:
                if key in result and isinstance(result[key], (int, float)):
                    val_num = result[key]
                    break
            val_raw = json.dumps(result, default=str)  # 序列化所有内容供 AI 使用
        else:
            # 字符串或其他
            val_raw = str(result)
            # 尝试强转 float
            try:
                val_num = float(result)
            except Exception:
                pass

        # 3. 插入记录
        TaskRecord.create(
            task=task_meta,
            value_num=val_num,
            value_raw=val_raw,
            timestamp=datetime.now(),
        )

    except Exception as e:
        logger.error(f"Failed to save DB record for {task_name}: {e}")
