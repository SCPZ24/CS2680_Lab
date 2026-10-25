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


## 硅基流动几乎要退避一分钟才能进入下一个iteration

### 观察

用户报告新一轮运行中，每个 iteration 都需要退避到第 6/7 次尝试。

### 改动

已在本机环境配置 OpenRouter API key，统一切换服务商。


## 缓存容器中的harness Python和依赖

### 观察

每次运行都会出现 uv 安装和 CPython 3.12 下载。原因是任务镜像缺少 Python 3.10+，而脚本把新解释器装在容器临时目录 `/tmp/uv-pythons`；`docker run --rm` 删除容器时，安装结果一并丢失。

### 改动

复用容器的环境和依赖，每次重新用容器运行仅初始化repo现场。
- `run_task.sh` 将 Docker 命名卷挂载到 `/tmp/uv-pythons`，持久保存 uv 安装的解释器及其 site-packages。启动时先探测缓存中可运行的 Python 3.12，再探测镜像内置解释器，最后才用 uv 安装。
- 另一个命名卷挂载到 `/root/.cache/pip`，复用依赖下载结果。
- 两类 volume 名称分别为 `madsloop-python-<image-id>`、`madsloop-pip-<image-id>`，按镜像内容 ID 隔离，避免不同镜像共享不兼容的安装环境。镜像内容改变时自动使用新缓存。
- `docker_env.sh` 改为使用 `${AGENT_PY:-python3}`。脚本先确保选定解释器拥有 openai，再运行 docker_env.sh；不再额外把 openai 装进任务镜像的默认 Python。每次仍执行 setup，以便依赖变更生效；setup 失败会停止启动。
- 不改变任务 shell 的 PATH，避免用 harness Python 替换项目的测试解释器；目标仓库仍来自每次新建的容器，不缓存 agent 对项目的修改。
- 关闭 pip 的版本升级检查，减少重复提示。

## task 0正式评测通过

用户将现有 task 0 补丁打包为仅含一条预测的 patches.json，并运行官方 evaluate.sh，返回 `RESOLVED` 和 `1/1 resolved`。本地 `pro_eval/eval_results.json` 中该 instance_id 的值确认为 true。

核对本次下载的官方评测器：它按 patches.json 中的有效 instance_id 选择评测任务，在评测环境中 reset/checkout 到 base_commit、应用补丁、执行评测准备命令和指定测试、解析测试输出。最终判定为 `(fail_to_pass | pass_to_pass) <= passed_tests`。

因此本次已确认的不只是 agent 调用 done，而是导出的补丁满足 task 0 的正式评分测试。1/1 的分母是此次提交评测的一个任务，不是课程全部十个任务；也不代表重复运行成功率或所有潜在边界都已验证。

当前这个任务的token总消耗：
- 输入：1,061,517
- 输出：6,418
- 总量：1,067,935

工具分布：bash 23、grep 6、read_file 13、glob 1、edit_file 2、done 1。

接下来开始以减少token消耗为目标继续优化

## 一轮调用多个工具

### 改动

请求加入 `parallel_tool_calls=true`，提示词鼓励同轮调用多个独立的只读工具。

### 结果

日志：`run_d9bf4d5a2e7946f1830ad8949dade7aa.jsonl`。

- 输入：1,230,974
- 输出：9,139
- 总量：1,240,113，比上轮增加172,178（16.12%）。
- 49轮API响应、53次工具调用；4轮各调用2个工具，其余45轮各1个。
- 无API重试，以done结束；本轮正式评测结果待确认。

批量调用已出现，但本次没有减少token消耗，不能据单次运行判断因果。
