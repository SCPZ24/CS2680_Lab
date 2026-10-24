# 为了完成 Part 2 — task 0

## 第一次运行因429退出

### 观察

硅基流动那边返回TPM超过rate limit，直接截断返回。

第一次报错直接退出程序。

### 改动

之后设置异常捕获机制：如果遇到返回TPM超过rate limit，则重试。

采用指数**指数退避**方案：等到1s、2s、4s、8s、16s、32s，实在不行则抛出错误。



## read_file默认读取全文件并设置硬末尾截断

### 观察

即使没有说明read_file工具可以指定行号，模型已经多次尝试读取指定区域(显示传入行号)。

本轮直接补齐读取能力。

### 改动

```python
read_file(path, start_line=1, end_line=None)
```

- 行号从 1 开始，起止行均包含在结果中。
- 不传参数时保持从首行开始读取；省略 end_line 表示读到文件末尾或输出上限。
- 输出使用原文件行号。end_line 超出文件范围时读到末尾；start_line 超出范围时明确提示。
- 拒绝非整数、布尔值、非正起始行和倒序范围；继续限制路径位于工作区内。
- 总输出上限仍为 12,000 字符，包含行号及截断提示。
- 普通截断保留完整行，并提示下一次的 start_line，使续读不漏行。
- 单行过长时明确标记行内截断，提示用 Bash 查看字符切片，不提供会重复卡在同一行的续读建议。
- 跳过前面的行及读取目标行时采用有长度限制的读取，避免一次加载整个文件或异常长行。

示例：

```json
{"path": "lib/ansible/utils/collection_loader/_collection_finder.py", "start_line": 840, "end_line": 875}
```


## 2026-10-24：从硅基流动切换到 OpenRouter

### 观察与决定

用户报告新一轮运行中，每个 iteration 都需要退避到第 6/7 次尝试，已在本机环境配置 OpenRouter API key，要求统一切换服务商。此处记录的是用户观察，尚未以最终日志统计量化频率；切换也不代表能够保证消除限流。

### 实际改动

- `src/integrations.py` 默认值与 `madsLoop.py` 入口统一使用 `https://openrouter.ai/api/v1`。
- 模型仍为 Qwen3.6-35B-A3B，改用 OpenRouter 的模型 ID `qwen/qwen3.6-35b-a3b`。
- 从 `OPENROUTER_API_KEY` 读取密钥；`run_task.sh` 与 `run_all.sh` 检查该变量，任务脚本将其传入 Docker。没有回退到其他服务商密钥。
- 保留非流式请求和关闭思考模式的基线意图；将硅基流动的 `enable_thinking` 请求字段替换为 OpenRouter 的 `reasoning.enabled=false`。实际供应商是否按预期执行，仍需真实调用观察。
- 同步本地运行 README、smoke test 和 CLI 离线测试的配置。历史日志、课程原始说明和本文此前的排查记录保留原样。
- 保持 1、2、4、8、16、32 秒退避、Retry-After 处理、工具、系统提示词和任务输入方式不变。

官方依据：[API 接入](https://openrouter.ai/developers)、[模型 ID](https://openrouter.ai/qwen/qwen3.6-35b-a3b/api)、[reasoning 参数](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)。

### 验证与后续

读取本机 shell 环境确认存在 `OPENROUTER_API_KEY`，未输出密钥值。14 项离线测试通过；新增 HTTP MockTransport 测试验证实际请求 URL、Bearer 认证、模型 ID、stream=false 和 reasoning.enabled=false，并确认缺少 OpenRouter key 时不会误用其他服务商密钥。两个运行脚本通过 bash 语法检查。

本轮没有发起真实付费模型请求或重跑容器评测。已启动的 agent 进程不会自动切换服务商，需要在下一次启动时加载新配置。比较实验时应单独标记服务商变化，不能把吞吐或解题表现的差异完全归因于 harness 逻辑。
