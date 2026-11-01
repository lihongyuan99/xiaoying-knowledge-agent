# 03 评估结果与限制

## 测试环境

- 日期：2026-11-01
- Python：3.13.12；仅使用标准库
- 离线测试：`PYTHONPATH=src python3 -m unittest discover -s tests -v`
- 真实模型：`deepseek-v4.1-flash`，本地 OpenAI 兼容服务 `http://127.0.0.1:8788/v1`
- 密钥仅通过进程环境变量使用，未写入运行记录或文档。

## 自动化测试

实际执行 12 项测试，全部通过；原始输出见 `examples/test-results.txt`。

| 测试 | 预期 | 实际 |
|---|---|---|
| `test_clear_answer` | 明确答案和有效 D04 引用 | 通过 |
| `test_conflict_lists_both_sources_and_asks` | 双方冲突、两条引用、缺口和追问 | 通过 |
| `test_out_of_scope_question` | 资料不足且无伪造引用 | 通过 |
| `test_paraphrased_question` | 改写问题仍定位 D03/500 元 | 通过 |
| `test_contextual_followup_rewrites_search` | 追问改写为上海住宿独立查询 | 通过 |
| `test_tool_failure_is_explicit_and_not_fabricated` | 注入读取失败并返回 `tool_error` | 通过 |
| `test_single_search_result_can_answer_without_invented_conflict` | 只有 D01 时回答 30 天 | 通过 |
| `test_replacing_document_changes_grounded_answer` | 替换资料后回答和引用改为 600 元 | 通过 |
| `test_invalid_citation_is_rejected_then_corrected` | 假引用被拒绝，反馈后修正 | 通过 |
| `test_step_limit_returns_explicit_incomplete_status` | 3 步后明确未完成 | 通过 |
| `test_multiple_json_actions_execute_only_first` | 连续 JSON 只执行首个动作 | 通过 |
| `test_non_json_trailing_content_is_rejected` | 首个动作后的非 JSON 内容仍报错 | 通过 |

这些测试使用离线 Mock 或测试序列模型，验证编排、工具、约束和解析逻辑；真实模型效果单独由下一节记录验证。

## 真实模型运行结果

以下均为真实 API 输出，不是手工编制的决策日志。索引见 `examples/real_manifest.json`；每条记录包含输入、实际输出、工具参数、观察、后续动作和简短依据。

| 文件 | 状态 | 步数 | 实际决策与结果 |
|---|---|---:|---|
| `examples/real_01_clear_answer.json` | `completed` | 3 | 检索后只读 D04，回答提前 3 个工作日，引用通过 |
| `examples/real_02_conflict.json` | `needs_clarification` | 4 | 读 D01 后继续读 D02，列出 30/15 天冲突并追问适用制度 |
| `examples/real_03_out_of_scope.json` | `insufficient_information` | 4 | 两次空命中后第三次改写；识别 D01 为无关命中，不编造宠物规定 |
| `examples/real_04_paraphrase.json` | `completed` | 3 | 将酒店每晚报销定位到 D03，回答北京 500 元 |
| `examples/real_05_followup.json` | `completed` | 3 | 将“那上海呢？”改写成上海出差住宿独立查询，回答 500 元 |
| `examples/real_06_tool_failure.json` | `completed` | 5 | 首次读 D03 失败后补充检索，再读原文并恢复；没有将失败当成功 |
| `examples/real_07_replaced_document.json` | `completed` | 3 | 使用替换版 D03，答案与引用均变为 600 元 |
| `examples/real_08_max_steps.json` | `max_steps_exceeded` | 1 | 达到 1 步上限，明确未完成且无答案引用 |

明确答案与冲突构成两条不同的真实 ReAct 路径；资料外问题的连续检索改写、工具失败后的恢复进一步体现模型根据观察调整动作。所有完成状态的引用均通过程序的文档 ID、已读取状态和逐字原文校验。

## 离线流程对照

`python3 scripts/generate_examples.py` 可复现 7 组 Mock 记录，索引为 `examples/manifest.json`。其中包含持续工具失败后返回 `tool_error` 的路径。Mock 仅验证流程与约束，不与上述真实模型结果混称。

## Web 界面验证

使用 WebKit 浏览器连接本地真实模型服务，实际点击示例并完成两轮独立会话：

- 明确答案：页面显示 D04 引用、版本日期和“检索 → 读取 → 完成”路径；截图 `docs/assets/ui-clear-answer.png`。
- 文档冲突：断言答案同时包含 30/15 天、恰好两条引用和一条追问，全部通过；截图 `docs/assets/ui-conflict.png`。
- 浏览器控制台共 0 条错误、0 条警告；1440 × 1000 视口下未见重叠或裁切。

## 实际失败与修复

1. **连续 JSON 导致解析失败**：首次真实运行冲突、资料外和改写用例时，模型在一次响应中输出多个 JSON 动作，出现 `Extra data`。修复为只解析并执行第一个完整动作，后续动作必须在获得新观察后重新决策；非 JSON 尾随内容仍拒绝。新增两项回归测试后复测，上述场景通过。
2. **资料缺口表述过度推断**：首次明确答案曾将“仅适用于常规年假”写入缺口，原文并不支持该限制。已在协议中要求 gaps 只能描述缺失信息、不能断言未说明的适用范围，并重新生成 `real_01_clear_answer.json`，最终结果无此推断。
3. **检索出现无关命中**：资料外问题第三次检索命中 D01，模型根据标题和片段判断其无关并报告资料不足。该行为正确，但也说明字符检索仍有噪声。

## 已知限制

1. **样本规模有限**：真实模型每个场景运行一次，不能代表所有问题、模型版本或服务配置的稳定性；提交前候选人应复跑并抽查。
2. **检索语义有限**：字符 n-gram 对同义词、跨语言和复杂语义改写较弱，且可能因通用词产生无关命中。
3. **语义校验不是完备证明**：程序保证引用逐字匹配，不能数学上证明所有自然语言推断正确，仍需人工审查。
4. **API 兼容性**：真实模式要求 Chat Completions 接口；不同服务的 JSON 响应能力可能仍需适配。
5. **无生产功能**：未实现认证、权限过滤、持久化、并发控制和敏感信息治理。
