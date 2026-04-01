# coverage-to-robot Demo Script

> 演示时长约 15-20 分钟。本文件包含每一步的口述要点、命令、预期输出，以及与 AI 交互的 prompt。
> 所有 demo 材料在 `demo/` 目录下，可离线运行（无需网络）。

---

## 准备工作

```bash
# 确认 Python 3 可用
python3 --version

# 确认 skill 目录结构
ls skills/coverage-to-robot/
#  SKILL.md  demo/  evals/  references/  scripts/

# 确认 demo 样本文件
ls skills/coverage-to-robot/demo/
#  sample_jacoco.xml   sample_lcov.html
```

---

## Demo 1: JaCoCo XML 解析（开发视角 — 快速概览）

### 口述要点
> "我们先看最简单的场景：开发同事跑完 UT 后拿到了一份 JaCoCo XML 报告。
> 只需要一条命令，就能把报告转成结构化的覆盖率 gap 清单。"

### 运行命令

```bash
python3 skills/coverage-to-robot/scripts/parse_coverage.py \
  skills/coverage-to-robot/demo/sample_jacoco.xml
```

### 预期输出关键点（讲解时挑重点说）

```
文件: BlockWriter.java
覆盖率: 33.3% line, 30.4% branch, 42.9% method

未覆盖的方法（4 个）:
  - flush()      — 18 行未覆盖, 8 个分支未覆盖  ← "这是 P0，核心 flush 逻辑完全没测到"
  - seal()       — 8 行未覆盖, 4 个分支未覆盖   ← "这也是 P0，seal 操作是关键路径"
  - getBlockId() — 1 行                         ← "这是 P3，trivial accessor，跳过"
  - toString()   — 2 行                         ← "这也是 P3，跳过"

已覆盖的方法（3 个）:
  - <init>()          — 3/3 行, 全覆盖
  - write()           — 12/13 行, 有 1 个分支未覆盖  ← "这是 P2，基本覆盖但有小分支 gap"
  - handleRotation()  — 5/15 行, 3 个分支未覆盖     ← "这是 P0-P1，部分覆盖但 gap 很大"
```

### 讲解
> "JaCoCo XML 给我们的是 **方法级** 的汇总数据：哪些方法完全没覆盖、哪些部分覆盖。
> 但它不告诉我们具体哪几行没覆盖。要看行级细节，需要 HTML 格式。
> 下面我们看 LCOV HTML 的解析。"

---

## Demo 2: LCOV HTML 解析（QA + 开发视角 — 深度分析）

### 口述要点
> "这是更常见的场景：CI 生成了 LCOV HTML 报告，我们能看到每一行的覆盖情况。
> 解析器会自动做三件事：识别未覆盖的连续代码块、归类到所属方法、按影响大小排序。"

### 运行命令

```bash
python3 skills/coverage-to-robot/scripts/parse_coverage.py \
  skills/coverage-to-robot/demo/sample_lcov.html
```

### 预期输出关键点（对着输出逐块讲解）

#### 2.1 Summary
```json
"summary": {
  "lines_hit": 30,  "lines_total": 78,  "line_coverage": 38.5
}
```
> "总覆盖率 38.5%，有很大提升空间。"

#### 2.2 partially_covered_methods（最高价值 — 重点讲）
```json
"partially_covered_methods": [
  { "name": "handleFlush",         "covered": 2,  "uncovered": 39, "coverage_pct": 4.9  },
  { "name": "handleCopyWriteResponse", "covered": 3,  "uncovered": 3,  "coverage_pct": 50.0 },
  { "name": "handleRotateBlock",   "covered": 7,  "uncovered": 2,  "coverage_pct": 77.8 }
]
```
> "**按未覆盖行数降序排列**，handleFlush 排第一——它有 39 行未覆盖，覆盖率只有 4.9%。
> 这意味着 UT 触碰了这个方法（进入了前两行），但核心的 flush 逻辑完全没走到。
> 这就是典型的 P0-P1 gap：部分覆盖的方法里藏着最大的 coverage 增长空间。"

#### 2.3 uncovered_blocks（定位到具体代码）
```json
"uncovered_blocks": [
  { "start_line": 133, "end_line": 161, "uncovered_line_count": 21, "containing_method": "handleFlush" },
  { "start_line": 195, "end_line": 203, "uncovered_line_count": 7,  "containing_method": "handleFlush" },
  { "start_line": 170, "end_line": 175, "uncovered_line_count": 5,  "containing_method": "handleFlush" },
  ...
]
```
> "最大的块是 handleFlush 里的 133-161 行（21 行），这就是异步 flush 到 SS 的核心逻辑。
> 第二大块 195-203 行是 flush 异常后的重试/恢复逻辑。
> **这些就是我们要用 E2E 测试去覆盖的目标。**"

#### 2.4 uncovered_methods（完全未覆盖）
```json
"uncovered_methods": [
  { "name": "handleFlushResponse", "start_line": 208, "end_line": 214, "uncovered_lines": 4 }
]
```
> "handleFlushResponse 完全没被调用过——说明整个 flush 链路都没跑通。
> 这和 handleFlush 的 gap 是相关的：flush 没触发，flush response 自然也不会有。"

---

## Demo 3: 从 Gap 到 Robot 测试用例（AI 协作 — 核心价值）

### 口述要点
> "现在我们拿到了结构化的 gap 数据，接下来是 skill 的核心价值：
> 把代码级的 gap 转化为可执行的 Robot Framework E2E 测试建议。
> 这一步由 AI 完成。让我现场演示。"

### Prompt（在 Devin / Windsurf 中输入）

```
我有一份 LCOV 覆盖率报告: skills/coverage-to-robot/demo/sample_lcov.html

请分析覆盖率 gap 并生成 Robot Framework 测试用例来提升覆盖率。
重点关注 handleFlush 方法（覆盖率 4.9%）。
```

### AI 预期输出的结构（对着实际输出讲解）

> AI 会生成一份完整的报告，结构如下：

```
# Coverage Analysis Report: AbstractBlockData

## Summary
| Metric | Hit | Total | Coverage |
|--------|-----|-------|----------|
| Lines  | 30  | 78    | 38.5%    |

## Coverage Gaps

### Gap 1: handleFlush — async flush to SS (lines 133-161)
- Severity: P0-P1
- Code path: BFW 接收到 FlushEvent 后，异步 flush 到 StorageServer
- Trigger: 写入 >= 64MB 对象触发 BFW flush

### Gap 2: handleFlush — flush error handling (lines 195-203)
- Severity: P1
- Code path: flush 失败后的异常处理 + block 不可写检查
- Trigger: 需要 fault injection (磁盘故障/网络断开)

### Gap 3: handleFlushResponse (lines 208-211)
- Severity: P0
- Code path: flush 回调处理
- Trigger: 任何成功的 flush 操作

## Robot Framework Test Cases

### coverage_abstract_block_data.robot
```

> "注意 AI 生成的用例有几个特点："
>
> 1. **遵循 `[Scenario-N]` 命名** + BDD Given/When/Then
> 2. **用了已有的 keyword**（`User Create Block`, `User Read Block Data`）
> 3. **Tag 里有 coverage 追踪标签**（`coverage-AbstractBlockData`, `coverage-handleFlush`）
> 4. **指定了正确的对象大小**（>= 64MB 才触发 flush）
> 5. **FI 测试标记了 `Standalone`**

---

## Demo 4: JaCoCo XML vs LCOV HTML 对比（30 秒快速切换）

### 口述要点
> "同一个类的覆盖率报告，两种格式的解析结果有什么区别？"

| 维度 | JaCoCo XML | LCOV HTML |
|------|-----------|-----------|
| 数据粒度 | 方法级 (哪个方法覆盖了多少行) | 行级 (具体哪一行没覆盖) |
| `uncovered_blocks` | 空 (XML 不含行信息) | 有 (连续未覆盖区域 + 所属方法) |
| `partially_covered_methods` | 空 (XML 无法区分) | 有 (覆盖百分比 + 按 gap 排序) |
| 适用场景 | 快速筛选哪些类/方法需要关注 | 深入分析具体要写哪些测试 |

> "建议工作流：先用 XML 做全局扫描，锁定目标类 → 再用 HTML 做深度分析。"

---

## Demo 5: URL 远程下载（如有网络）

### 口述要点
> "实际工作中，覆盖率报告通常在 CI 服务器上。
> 解析器支持直接传 URL，不需要手动下载。"

### 命令（如果有内网访问）

```bash
# 直接传 CI 上的 LCOV HTML URL
python3 skills/coverage-to-robot/scripts/parse_coverage.py \
  "https://ci.example.com/coverage/reports/..."
```

> "解析器会自动下载、检测格式、解析、输出 JSON。和本地文件完全一样的输出。"

---

## 收尾：总结 & Q/A 引导

### 给开发同事的要点
- 你跑完 UT 后，用这个工具快速看哪些 gap 值得用 E2E 补
- 重点看 `partially_covered_methods`（比完全未覆盖的方法更有价值）
- AI 生成的触发条件映射需要你 review（你最清楚这个方法怎么被调用到）

### 给 QA 同事的要点
- AI 生成的 Robot 用例遵循了团队规范（keyword、tag、命名），但需要你 review 可执行性
- 关注对象大小是否正确（< 44MB / >= 64MB / >= 128MB 走不同路径）
- FI 测试需要标 `Standalone`，并确认环境是否支持

### 可以问的 Q/A 问题
1. "如果我的报告格式不是 JaCoCo 或 LCOV 怎么办？"
   → 可以手动分析 HTML，skill 有 fallback 指引
2. "生成的用例能直接跑吗？"
   → 需要人工 review，主要确认触发条件和环境依赖
3. "如果映射表里没有我这个类/方法怎么办？"
   → robot_patterns.md 目前主要覆盖 blocklayer，其他区域可以按需扩展
