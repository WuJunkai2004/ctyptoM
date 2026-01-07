# CryptoM - 强大的加密货币监控工具

CryptoM 是一个基于 Python 的异步加密货币市场监控和自动化工具。利用 `ccxt` 库的强大功能，它允许用户通过简单的 YAML 配置文件定义监控任务、数据获取逻辑和自动化操作。

## 主要特性

*   **多交易所支持**: 基于 `ccxt`，支持所有主流加密货币交易所（Binance, OKX, Bybit 等）。
*   **灵活的配置**: 使用 YAML 文件定义交易所连接和监控任务。
*   **强大的任务引擎**:
    *   支持定时执行任务。
    *   支持任务依赖（一个任务的输出可以作为另一个任务的输入）。
    *   支持 Python 表达式对数据进行实时处理和计算。
*   **自动化操作**:
    *   基于条件触发日志记录。
    *   基于条件触发自定义 Python 脚本（Action），实现下单、通知等复杂逻辑。
*   **高性能**: 基于 `asyncio` 和 `uvloop` (通过 `uvicorn` 或默认事件循环) 构建，并发处理多个任务。

## 安装

### 前置要求

*   Python 3.10 或更高版本

### 安装步骤

本项目使用 `uv` 进行依赖管理，也可以使用 `pip`。

1.  **克隆仓库**

    ```bash
    git clone https://github.com/yourusername/cryptom.git
    cd cryptom
    ```

2.  **安装依赖**

    如果你使用 `uv`:
    ```bash
    uv sync
    ```

    或者使用 `pip`:
    ```bash
    pip install .
    ```

## 快速开始

1.  **创建配置文件**

    在项目根目录下创建一个 `config.yaml` 文件。以下是一个简单的示例，用于监控币安上 BTC/USDT 的价格，并在价格超过 50000 时打印日志：

    ```yaml
    # config.yaml
    port: 16888

    exchanges:
      - name: binance
        enableRateLimit: true
        # 如果需要私有接口，取消注释并填入 API Key
        # apiKey: "YOUR_API_KEY"
        # secret: "YOUR_SECRET_KEY"

    tasks:
      - name: fetch_btc
        exchange: binance
        function: fetch_ticker
        args: ["BTC/USDT"]
        interval: 5  # 每 5 秒执行一次
        return: "fetch_btc['last']" # 提取最新价格

      - name: monitor_btc
        dependencies: ["fetch_btc"] # 依赖 fetch_btc 任务
        interval: 5
        # 使用 Python 表达式判断条件
        condition: "fetch_btc > 50000"
        log: "BTC Price Alert: Current price is {fetch_btc}"
    ```

2.  **运行程序**

    ```bash
    cryptom -c config.yaml
    ```

    或者作为模块运行：

    ```bash
    python -m src.cryptom -c config.yaml
    ```

## 详细配置指南

配置文件主要包含两部分：`exchanges`（交易所配置）和 `tasks`（任务配置）。

### 1. 交易所配置 (`exchanges`)

定义需要连接的交易所及其认证信息。

```yaml
exchanges:
  - name: okx
    apiKey: "YOUR_API_KEY"
    secret: "YOUR_SECRET_KEY"
    password: "YOUR_PASSPHRASE" # OKX 需要
    options:
      defaultType: "swap" # 例如设置默认交易类型为永续合约
```

### 2. 任务配置 (`tasks`)

任务是 CryptoM 的核心。它可以获取数据、计算指标或执行操作。

| 字段 | 说明 |
| :--- | :--- |
| **Data Fetching** | |
| `name` | 任务的唯一标识符，后续任务可通过此名称引用其结果。 |
| `exchange` | 指定使用的交易所名称（必须在 `exchanges` 中定义）。 |
| `function` | 调用的 `ccxt` 方法名（例如 `fetch_ticker`, `fetch_balance`）。 |
| `args` | 传递给函数的参数列表（例如 `["BTC/USDT"]`）。 |
| `kwargs` | 传递给函数的关键字参数字典。 |
| **Logic & Scheduling** | |
| `interval` | 任务执行间隔（秒）。如果一个任务没有设置此项，则只能通过其他依赖的任务触发执行。 |
| `dependencies` | 依赖的其他任务名称列表。依赖任务的结果可在 `return`, `condition`, `log` 等表达式中作为变量使用。 |
| `return` | Python 表达式。用于处理任务结果。例如 `task_result['last']`。默认返回原始结果。 |
| **Actions** | |
| `condition` | Python 表达式（返回布尔值）。当结果为 `True` 时，执行 `log` 和 `action`。如果未设置，则默认总是执行。 |
| `log` | 格式化字符串。当条件满足时打印的日志信息。支持使用 `{task_name}` 引用依赖数据。 |
| `action` | Python 脚本文件的路径。当条件满足时执行该脚本。 |

### 配置示例：套利监控

监控两个交易所的价差：

```yaml
tasks:
  - name: binance_btc
    exchange: binance
    function: fetch_ticker
    args: ["BTC/USDT"]
    interval: 2
    return: "binance_btc['last']"

  - name: okx_btc
    exchange: okx
    function: fetch_ticker
    args: ["BTC/USDT"]
    interval: 2
    return: "okx_btc['last']"

  - name: arb_monitor
    dependencies: ["binance_btc", "okx_btc"]
    interval: 2
    condition: "abs(binance_btc - okx_btc) > 100"
    log: "Arbitrage opportunity! Diff: {binance_btc - okx_btc:.2f}"
    action: "scripts/notify_arb.py"
```

## 自定义脚本 (Actions)

你可以编写 Python 脚本来执行复杂的自定义操作（如发送通知、下单等）。脚本需要定义一个被 `@register` 装饰的函数。

**示例脚本 (`scripts/my_action.py`):**

```python
from cryptom.action import register

# 注册一个 action
# 装饰器会自动注入 context (包含依赖任务的数据) 和 exchange (当前任务关联的交易所对象)
@register
def execute_trade(context, exchange):
    # 获取上下文中的数据
    price = context.get('fetch_btc')
    print(f"Action triggered! Current BTC price: {price}")
    
    # 你可以在这里调用 exchange 的方法进行交易
    # exchange.create_market_buy_order('BTC/USDT', 0.001)
```

在 `config.yaml` 中引用：

```yaml
tasks:
  - name: ...
    action: "scripts/my_action.py"
```

## 路线图

*   **Web 可视化界面**: 计划开发基于网页的用户界面 (Web UI)，为用户提供更直观的数据浏览、图表展示和任务管理体验。
*   **AI 智能分析**: 计划集成人工智能算法，对采集的市场数据进行深度分析，辅助识别趋势和交易机会。

## 命令行参数

```text
usage: cryptom [-h] [-c CONFIG] [-l LOG_LEVEL] [-t TTL]

CryptoM service command line interface

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        Path to the configuration file (default: config.yaml)
  -l LOG_LEVEL, --log-level LOG_LEVEL
                        Logging level (default: INFO)
  -t TTL, --ttl TTL     Time to live for cached data in seconds (default: 60)
```

## 开发与贡献

欢迎提交 Issue 和 Pull Request！

## License

MIT
