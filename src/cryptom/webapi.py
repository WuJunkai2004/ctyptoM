# src/cryptom/api.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from .config import AppConfig
from .engine import CryptoEngine
from .stored import fetchGraphData


class RunTaskRequest(BaseModel):
    taskName: str


def createApp(config_str: Path):
    engine = CryptoEngine()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Load configuration
        logger.info(f"Loading config file: {config_str}")
        config_path = Path(config_str)
        if not config_path.exists():
            logger.error(f"cannot find config file: {config_path.absolute()}")
            return
        config = AppConfig.load(config_path)
        logger.info("Config file loaded successfully.")

        await engine.init(config)
        engine.scheduler.start()

        # 将 engine 和 config_path 挂载到 app.state 以便接口调用
        app.state.engine = engine
        app.state.config_path = config_path

        yield

        # --- Shutdown ---
        await engine.stop()

    app = FastAPI(lifespan=lifespan)

    # 1. 重载配置
    @app.post("/api/config/reload")
    async def reload_config(request: Request):
        config_path: Path = request.app.state.config_path

        # 停止旧引擎
        await engine.stop()

        try:
            # 加载新配置
            new_config = AppConfig.load(config_path)
            await engine.init(new_config)
            engine.scheduler.start()

            # 更新状态
            return {
                "status": "success",
                "message": "Configuration reloaded successfully",
            }
        except Exception as e:
            # 如果重启失败，尝试重启旧引擎（可选），或者直接报错
            return {"status": "error", "message": f"Failed to reload: {str(e)}"}

    # 2. 获取任务列表
    @app.get("/api/config/getTaskList")
    async def get_task_list(
        request: Request,
        intervalable: bool = Query(False),
        loggable: bool = Query(False),
        activable: bool = Query(False),
    ):
        tasks = []

        for name, task_engine in engine.tasks.items():
            # 筛选逻辑
            if intervalable and not task_engine.interval:
                continue
            if loggable and not task_engine.log:
                continue
            if activable and not task_engine.script:
                continue
            tasks.append(name)

        return {"tasks": tasks}

    # 3. 立即运行任务
    @app.post("/api/config/runTask")
    async def run_task(request: Request, body: RunTaskRequest):
        task_name = body.taskName

        task = engine.tasks.get(task_name)
        if not task:
            return {"success": False, "message": f"Task '{task_name}' not found"}

        try:
            # 异步调度任务，不等待结果返回，避免阻塞接口
            # 如果需要等待结果，可以用 await task.execute()
            # 这里选择后台触发
            import asyncio

            asyncio.create_task(task.execute())
            return {"success": True, "message": f"Task '{task_name}' triggered"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    # 4. 获取图表列表
    @app.get("/api/graph/getGraphList")
    async def get_graph_list(request: Request):
        if not engine.config or not engine.config.graphs:
            return {"graphs": []}
        engine: CryptoEngine = request.app.state.engine
        graphs = [g.name for g in engine.config.graphs]
        return {"graphs": graphs}

    # 5. 获取图表数据
    @app.get("/api/graph/getGraphData")
    async def get_graph_data(
        request: Request,
        graphName: str = Query(...),
        startTime: float = Query(...),
        endTime: float = Query(...),
    ):
        if not engine.config or not engine.config.graphs:
            raise HTTPException(status_code=404, detail="No graphs configured")
        # 查找图表配置
        graph_config = next(
            (g for g in engine.config.graphs if g.name == graphName), None
        )
        if not graph_config:
            raise HTTPException(status_code=404, detail="Graph not found")

        # 获取该图表涉及的所有 task names (y_axis)
        task_names = graph_config.y_axis

        # 查询数据库
        series_data = fetchGraphData(task_names, startTime, endTime)

        return {
            "axis": {"x": graph_config.x_axis, "y": graph_config.y_axis},
            "data": series_data,
            "description": graph_config.description or graph_config.title,
        }

    return app
