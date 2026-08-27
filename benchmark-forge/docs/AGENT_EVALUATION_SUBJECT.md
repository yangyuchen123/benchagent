# Benchmark 的被测对象：Agent，而不是裸 LLM

## 核心定义

Benchmark Forge 评测的对象是一个完整 Agent：

```text
LLM
+ host runtime
+ native tools
+ workspace/filesystem
+ memory/context management
+ subagent lifecycle
+ trajectory and artifacts
```

Benchmark 环境的职责是提供：

```text
受控任务数据
业务状态与状态转移
故障与扰动
访问边界/阶段门控
独立 verifier
可观察 evidence
```

环境不能重写或模拟正在被测的 Agent 原生能力。否则测量对象会退化为：

```text
LLM 是否会按一个手写 API 生成参数或 JSON
```

## 三种 Tool ownership

### `benchmark_environment`

由 bundle 实现的确定性任务世界工具，例如：

```text
订单同步服务
故障注入
业务记录查询
服务端状态检查
受控语料访问
```

它们不是被测能力本身，而是 Agent 操作的外部世界。

### `agent_runtime`

由被测 Agent/host 已有能力提供，例如：

```text
subagent_spawn/message/wait/trace
workspace/filesystem
native memory/context management
multi-turn control
```

Benchmark bundle 不得实现或模拟这些工具，只能通过 eval-system 输出的 host trajectory、artifact 和状态证据观察它们。

### `evaluation_system`

由评测系统持有且不能暴露给被测 Agent，例如：

```text
hidden oracle
independent verifier
attempt evidence collector
fault schedule authority
```

## 典型错误

错误的 delegation Benchmark：

```text
环境手写 subagent_spawn
→ 返回 synthetic child status/output
→ scorer 检查调用参数
```

它测的是 LLM 的工具调用格式，不是真实任务委派。

正确方向：

```text
被测 Agent 调用自己的 native subagent
→ eval-system 记录真实 parent/child trace
→ Benchmark verifier 检查任务拆解、上下文指派、并行性、验收、修复和整合
```

错误的 native context-compression Benchmark：

```text
环境提供 memory_write/memory_read
→ LLM 主动写摘要并读回
```

这只能称为“受控外部记忆压缩任务”，不能直接代表 Agent 自己的上下文压缩保真度。

正确方向：

```text
长任务运行
→ host/eval-system 触发或观察真实 context compaction/reset
→ Agent 使用自己的 observation/memory 恢复
→ 根据恢复后的行为、artifact 和 trace 评分
```

## 当前机械约束

- `ToolContract` 和 `IRTool` 增加 typed `ownership`。
- IR Compiler 必须显式区分三类 ownership。
- Runtime Component 只能实现 `benchmark_environment` tools。
- Tests 不得要求 generated runtime 暴露 `agent_runtime` tools。
- Static validator 拒绝环境模拟 subagent lifecycle。
- Native context-compression 目标中，Static validator 拒绝用环境 `memory_write/memory_read` 替代被测 Agent 的真实 context management。
