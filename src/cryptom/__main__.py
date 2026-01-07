# src/cryptom/__main__.py
import argparse
from loguru import logger

def start(config: str = "config.yaml"):
    """
    启动监控服务
    """
    logger.info(f"正在加载配置文件: {config}")
    logger.info("系统启动中...")
    # 这里未来会调用你的 Engine 和 Server
    # from .engine import run_engine
    # run_engine(config)


app = argparse.ArgumentParser(description="CryptoM 监控服务")
app.add_argument(
    "-c", "--config",
    type=str,
    default="config.yaml",
    help="配置文件路径，默认为 config.yaml",
)
app.set_defaults(func=start)

def main():
    args = app.parse_args()
    args.func(args.config)


if __name__ == "__main__":
    main()
