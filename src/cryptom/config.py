from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field, model_validator


# --- 1. 交易所配置 ---
class _ExchangeConfig(BaseModel):
    model_config = {"extra": "allow"}
    name: str
    apiKey: Optional[str] = None
    secret: Optional[str] = None
    password: Optional[str] = None  # OKX 需要
    enableRateLimit: bool = True
    # 允许额外的配置项传给 ccxt (比如 options)
    options: Dict[str, Any] = Field(default_factory=dict)


# --- 2. 任务配置 ---
class TaskConfig(BaseModel):
    name: str

    # --- 数据获取类任务字段 ---
    exchange: Optional[str] = None
    function: Optional[str] = None

    # 允许 YAML 里写 params: "BTC/USDT" (简写) 或 args: [...]
    args: List[Any] = Field(default_factory=list)
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    # 兼容旧写法 params，自动转为 args
    params: Optional[Union[str, List[Any]]] = Field(None, exclude=True)

    # --- 逻辑计算类任务字段 ---
    dependencies: List[str] = Field(default_factory=list)
    interval: Optional[int] = None  # 如果依赖任务是被动触发，这里可以为空

    # --- 表达式与后续动作 ---
    # return_expr: Python 表达式字符串，用于提取或计算结果
    return_expr: Optional[str] = Field(None, alias="return")
    # condition: Python 表达式字符串，返回 Bool
    condition: Optional[str] = None
    # log: 格式化字符串, 当 condition 为 True 时记录日志
    log: Optional[str] = None
    # action: 触发脚本路径, 当 condition 为 True 时执行
    action: Optional[str] = None

    @model_validator(mode="before")
    def compatible_params(cls, values):
        """兼容性处理：把 params 字段自动挪到 args 里"""
        params = values.get("params")
        if params is None:
            return values
        if isinstance(params, list):
            # 如果 params 已经是列表，视为 args
            values.setdefault("args", []).extend(params)
        elif isinstance(params, (str, int, float, bool)):
            # 如果 params 是单个基本类型，放入 args
            values.setdefault("args", []).append(params)
        elif isinstance(params, dict):
            # 如果 params 是字典，视为 kwargs
            values.setdefault("kwargs", {}).update(params)
        return values


# --- 3. 图表配置 (暂时预留) ---
class _GraphConfig(BaseModel):
    name: str
    type: str
    title: str
    x_axis: str
    y_axis: List[str]
    description: Optional[str] = None


# --- 4. 根配置 ---
class AppConfig(BaseModel):
    port: int = 16888
    exchanges: List[_ExchangeConfig]
    tasks: List[TaskConfig]
    graphs: List[_GraphConfig] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    def get_exchange_config(self, name: str) -> Optional[_ExchangeConfig]:
        for ex in self.exchanges:
            if ex.name == name:
                return ex
        return None
