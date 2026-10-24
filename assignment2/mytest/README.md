# 自建任务（待实现）

本目录对应作业 Part 2.6。`../tests/` 中的 harness 单元测试和简单 API 联调项目不计入这里要求的四个任务。

需要补齐：

| 文件 | 要求 |
| --- | --- |
| `solved_after_change.json` | 两个改进 harness 后才解决的任务 |
| `unsolved.json` | 两个提交时 harness 仍未解决的任务 |
| `task_test.json` | 四个任务的评测配置 |
| `tasks/<instance_id>/Dockerfile` | 固定代码版本并预装测试依赖 |
| `tasks/<instance_id>/gold.diff` | 能通过测试的真实修复 |
| `verify.sh` | 在全新容器中应用补丁并验证 |
| `results/<instance_id>.before.log` | 四个基线运行记录 |
| `results/<instance_id>.after.log` | 四个修复后运行记录 |

任务来源、验证规则和字段格式以 `../project_description_assignment.md` 的 Part 2.6 为准。当前仅建立目录，不创建空任务 JSON 或假验证脚本。
