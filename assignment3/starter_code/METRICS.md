# 本地测评指标

运行 `python3 evaluation_scripts/run_all.py --limit 1` 测试第一道题；省略 `--limit` 顺序运行全部任务。
当前 dispatcher 固定最多一个在飞任务，`A3_MAX_LIVE` 不再生效。当前任务完成（包括返工、评测和清理）后才能领取下一题。
`Agent.solve()` 的 `messages` 是任务局部变量；同一道题返工可保留历史，切换题目必须新建历史。模型 HTTP 客户端可以复用，它不保存对话消息。
目前 `solve()` 仍是课程提供的空骨架，尚未实现实际解题循环。

## 输出

- `run_all_results.md`：逐题结果、评测次数、失败评测次数、返工次数、E2E、token 和美元成本，以及整轮汇总。
- `run_logs/metrics.json`：上述指标的结构化数据、完整性标记，以及本次模型名称和价格快照；不包含 API key 或 base URL。
- `run_logs/evaluations.tsv`：原有的逐次评测记录。
- `run_logs/task_times.tsv`：原有的任务起止墙钟时间；开始时间在沙盒启动后，与新增 E2E 的起点不同。
- `madsOpt_logs/<k>/run.jsonl`：模型和工具调用日志；运行中原始文件在 `.tasks/trace/madsOpt_logs/<k>/run.jsonl`，汇总直接读取该原始文件。

## 口径

`rework_count` 只在已完成的评测失败之后，接受 `continue_task(k)` 时加一。
失败后直接结束不算返工；没有新评测而连续调用 continue 不重复计数；成功后 continue 不算失败返工。
因此它不等于原有的 `attempt`，后者从 0 开始，每次 continue 都会增加。
`evaluations` 是实际完成的评分流程数量，`failed_evaluations` 是其中未解决的次数。
`done()` 自动触发的评分也计入；包括从未评测且无补丁时由脚手架执行的空补丁失败评分。

逐题 `e2e_seconds` 使用单调时钟，从 dispatcher 接受 next_task 请求、准备启动沙盒开始，到最终评分及沙盒/补丁目录清理结束。
它包含推理、工具执行、失败返工、评测排队与执行、容器启动和清理。
整轮 E2E 是第一题开始到最后一题结束，包含两题之间的间隔，不含首题之前的镜像准备/公共网络启动和末题之后的公共设施清理。
没有领取的任务 E2E 为 null，token/成本为 0，并标记 opened=false。

`input`、`cache`、`output` 统一为 **美元 / 100 万 token**；cache 是缓存命中输入价格。
价格字段可留空，已填写的值须为有限、非负数。缺失档位仍静默跳过，多余档位仍忽略。

```text
每次请求成本 = ((prompt_tokens - cached_prompt_tokens) × input
                + cached_prompt_tokens × cache
                + completion_tokens × output) / 1,000,000
```

`prompt_tokens` 已包含缓存命中 token，`completion_tokens` 按上游返回值使用，推理 token 不重复累加。
如果上游未返回 `prompt_tokens_details.cached_tokens`，按没有报告缓存命中处理，即 0。
模型调用必须经过 `call_model(..., model_tier="standard")` 或写入等价的 `logger.api_request()` 事件。
传入 model_tier 可以区分“同一模型名称、不同档位/上游价格”；省略时只有模型名称唯一匹配才计价。

缺少 usage、必要单价、唯一的模型匹配，或日志不完整时，`cost_usd` 为 null，Markdown 显示 unknown。
`known_cost_usd` 保留可完整计价请求的小计，token 字段保留有效 usage 的小计；`usage_complete` 和 `cost_complete` 表示能否作为完整统计使用。
没有调用模型且日志正常结束时，成本为 0。只有实际使用的 token 类别才需要对应价格，例如没有缓存命中时不需要 cache 价格。

这些成本是按本次配置和已记录 usage 计算的估算值，不是上游账单对账结果；不会捕获绕过日志的调用、请求失败后未返回的计费用量或 SDK 内部重试的计费。
正常结束及 dispatcher 时间限制后的收尾会写汇总；沿用脚手架行为，手动中断运行不生成最终汇总，可保留的原始日志仍在运行目录中。

## 离线检查

```bash
python3 -B -m unittest discover -s tests -v
```

不需要 Docker、不调用真实模型、不产生 API 费用。
