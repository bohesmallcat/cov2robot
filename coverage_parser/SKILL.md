---
name: coverage-to-robot
description: Analyze Java test coverage reports (JaCoCo XML/HTML, LCOV HTML) and generate Robot Framework end-to-end test case suggestions to improve coverage. Use this skill whenever the user asks to analyze code coverage, improve test coverage, generate E2E tests from coverage data, convert coverage gaps into Robot Framework cases, or mentions JaCoCo/LCOV reports in the context of test improvement. Also trigger when the user asks about uncovered code paths, coverage gaps, or wants to know what integration/system tests to add.
---

# Coverage-to-Robot: Test Coverage Analysis & Robot Framework Case Generator

Analyze Java test coverage reports and produce actionable Robot Framework E2E test case suggestions targeting the uncovered code paths.

## Supported Input Formats

1. **JaCoCo XML** (`jacoco.xml`) — richest data: line, branch, method, class-level counters
2. **JaCoCo HTML** (single-class `.html` from JaCoCo report) — line-level hit/miss with source
3. **LCOV HTML** (single-class `.html` from LCOV/gcov report) — line-level hit/miss with source

The parser accepts both **local file paths** and **HTTP/HTTPS URLs**.  When given a URL (e.g. a link to an internal CI server or any hosted LCOV/JaCoCo report), the file is downloaded automatically before parsing.

## Workflow

### Step 1 — Parse the Coverage Report

Run the parser script to extract structured coverage data:

```bash
# Local file
python <skill-dir>/scripts/parse_coverage.py <path-to-report> [--format auto|jacoco-xml|jacoco-html|lcov-html]

# Remote URL (downloaded automatically)
python <skill-dir>/scripts/parse_coverage.py <url-to-report> [--format auto|jacoco-xml|jacoco-html|lcov-html]
```

The script outputs JSON to stdout with this structure:
```json
{
  "file": "com/example/MyClass.java",
  "summary": {
    "lines_hit": 39, "lines_total": 104, "line_coverage": 37.5,
    "branches_hit": 10, "branches_total": 50, "branch_coverage": 20.0
  },
  "uncovered_methods": [
    {"name": "handleFlush", "start_line": 132, "end_line": 203, "covered_lines": 0, "uncovered_lines": 45}
  ],
  "covered_methods": [
    {"name": "write", "start_line": 50, "end_line": 80, "covered_lines": 20, "uncovered_lines": 0}
  ],
  "partially_covered_methods": [
    {"name": "read", "start_line": 90, "end_line": 130, "covered_lines": 25, "uncovered_lines": 10, "coverage_pct": 71.4}
  ],
  "uncovered_blocks": [
    {"start_line": 140, "end_line": 165, "uncovered_line_count": 20, "containing_method": "handleFlush"}
  ],
  "uncovered_lines": [...],
  "uncovered_branches": [...]
}
```

**Key output fields:**
- `uncovered_methods` — methods with zero coverage (entirely missed)
- `partially_covered_methods` — methods with *some* coverage but significant uncovered branches/blocks; sorted by uncovered lines descending (worst gaps first); includes `coverage_pct`
- `covered_methods` — fully covered methods
- `uncovered_blocks` — contiguous regions of uncovered lines grouped together, each mapped to its containing method name; sorted by size descending (largest gaps first)

If the script fails or the format is unsupported, fall back to manual analysis: read the HTML file, extract line-level coverage from CSS classes (`lineCov`/`lineNoCov`/`branchCov`/`branchNoCov`), and build the coverage map yourself.

### Step 2 — Identify Coverage Gaps

Use the parser output to classify gaps by severity and actionability for E2E tests.  In practice, the **most valuable gaps are in partially-covered methods** (uncovered error-handling branches, edge-case `if` paths inside otherwise-hit methods).  These often represent the largest share of uncovered lines.

| Priority | Gap Type | Where to Look | E2E Testable? |
|----------|----------|---------------|---------------|
| **P0** | Entire uncovered method (0 hits) with business logic | `uncovered_methods` | Yes — needs a scenario that triggers this code path |
| **P0-P1** | Large uncovered block inside a partially-covered method | `uncovered_blocks` with `containing_method` set, cross-ref with `partially_covered_methods` | Yes — needs specific conditions/errors to hit that branch |
| **P1** | Uncovered error/exception branches | `uncovered_blocks` whose source contains `catch`, `throw`, error-handling | Yes — needs fault injection or negative test |
| **P2** | Small uncovered branches within well-covered methods | `partially_covered_methods` with high `coverage_pct` (>80%) | Maybe — depends on reachability via external API |
| **P3** | Trivial accessors/toString/logging with 0 hits | `uncovered_methods` with small `uncovered_lines` count | Low value — skip unless specifically requested |

**Workflow for each gap:**
1. **Start from `uncovered_blocks`** (sorted largest-first) — these are the highest-impact targets
2. **Cross-reference with `partially_covered_methods`** — a method at 14% coverage with 85 uncovered lines is more impactful than a method with 2 uncovered lines
3. **Read the source code** of each uncovered block to understand what it does
4. **Trace the call chain** — how does an external operation (S3 PUT, GET, DELETE, replication, etc.) reach this code?
5. **Identify the trigger** — what specific user-facing operation and conditions would exercise this path?

### Step 3 — Map Gaps to E2E Scenarios

For each significant coverage gap, determine:
- **What external operation triggers it** (S3 PUT, multipart upload, copy, geo-replication, etc.)
- **What preconditions are needed** (encryption enabled, versioning, specific object size, fault injection)
- **What assertions verify the path was exercised** (object integrity, error codes, block state)

### Step 4 — Generate Robot Framework Test Cases

Read `references/robot_patterns.md` for the Storage Platform Automation Robot Framework conventions, keyword libraries, and test patterns.

For each E2E test case, generate Robot Framework syntax following these rules:

1. **Use existing keywords** — search `references/robot_patterns.md` for the right keywords from `SharedObjectKeywords`, `EngineServerManagerKeywords`, `acceptance_keywords.robot`, `data_path_services_keywords.robot`, and `general_s3_bucket_keywords.robot`. Common keywords include:
   - `The storage cluster Cluster Is Reachable`, `User Has Access to The Data Node`, `All Nodes Are Online`
   - `Prepare for S5CMD Client`, `Cleanup S5CMD Client`, `Create Bucket Through S5CMD`
   - `Ingest Objects To Bucket Through S5CMD`, `Enable DT Query`, `Disable DT Query`
2. **Follow the `[Scenario-N]` naming convention** — number tests sequentially, use BDD Given/When/Then style
3. **Include Setup/Teardown** — use `Run Keywords ... AND ...` chaining; always clean up created resources
4. **Tag with coverage metadata** — add `[Tags]` with tier (`Tier1`/`Tier2`), concurrency (`Parallel`/`Standalone`), and coverage-specific tags (e.g., `Coverage    coverage-AbstractBlockData    coverage-handleFlush`)
5. **Import libraries with full dotted path** — e.g., `Library    library.object.engine.engine_manager_keywords.EngineManagerKeywords`
6. **Use the correct Suite Setup/Teardown** — `Establish Connection To Platform    ${PLATFORM}` / `Close Clients`
7. **Group related tests** into a single `.robot` file per component/class

### Output Format

Present results as a structured report:

```markdown
# Coverage Analysis Report: <ClassName>

## Summary
| Metric | Hit | Total | Coverage |
|--------|-----|-------|----------|
| Lines  | ... | ...   | ...%     |
| ...    | ... | ...   | ...%     |

## Coverage Gaps

### Gap 1: <method/block description>
- **Lines**: X-Y
- **Severity**: P0/P1/P2
- **Method**: <containing_method> (coverage: N%)
- **Code path**: <brief description of what the uncovered code does>
- **Trigger**: <external operation that would exercise this path>

## Partially Covered Methods (sorted by uncovered lines)
| Method | Lines | Covered | Uncovered | Coverage | Top Uncovered Block |
|--------|-------|---------|-----------|----------|---------------------|
| read   | 1436-1755 | 109 | 90 | 54.8% | lines 1705-1732 (remote read fallback) |

## Robot Framework Test Cases

### <test-file-name>.robot

\`\`\`robotframework
*** Settings ***
Resource    robot/common_keywords/acceptance_keywords.robot
Resource    robot/common_keywords/data_path_services_keywords.robot

Library     library.object.engine.engine_manager_keywords.EngineManagerKeywords

Default Tags    storage_driver    Coverage

Suite Setup       Establish Connection To Platform    ${PLATFORM}

Test Setup        Run Keywords
...                   The storage cluster Cluster Is Reachable
...    AND            All Nodes Are Online

Test Teardown     Run Keywords
...                   Stop Data Ingestion
...    AND            All Nodes Are Online

Suite Teardown    Close Clients

*** Variables ***
${OBJECT_COUNT}    ${1000}

*** Test Cases ***
[Scenario-N] <DescriptiveTestName>
    [Documentation]    As an storage cluster customer, I want to <action>,
    ...     So that I can validate <feature>.
    ...     Coverage gap: <class>.<method> (lines X-Y)
    [Tags]    Tier2    Parallel    E2E    Coverage    coverage-<ClassName>    coverage-<methodName>
    Given The storage cluster Cluster Is Reachable
      And User Has Access to The Data Node
     When <Trigger Keyword>
     Then <Verification Keyword>
\`\`\`

## Priority Matrix
| Priority | Test Case | Coverage Gap | Est. Line Gain |
|----------|-----------|-------------|----------------|
| P0       | ...       | ...         | ~N lines       |
```

### Step 5 — Cross-Reference with Existing Tests

Before finalizing, check if any suggested scenarios are already covered by existing Robot Framework tests:

1. Search the `robot/` directory tree in the automation repo for similar test patterns:
   - `robot/object/storagedriver/*.robot` — storage driver acceptance and fault injection suites
   - `robot/object/blocklayer/*.robot` — block-layer test suites
   - `robot/object/replication/*.robot` — replication test suites
   - `robot/object/spacereclaim/*.robot` — space-reclaim / GC suites
   - `robot/common_keywords/*.robot` — shared keyword definitions
2. Also search the Python keyword libraries under `library/object/` for keywords that already cover the suggested test action
3. If a test already exists, note it and suggest modifications instead of new tests
4. Focus suggestions on genuinely missing scenarios

## Important Guidelines

- **E2E tests, not unit tests** — every suggested test must be executable against a running storage cluster via S3 API or management API
- **Be specific about object sizes** — many code paths (block rotation, flush, multi-block) only trigger above certain size thresholds (see `references/robot_patterns.md` § Object Size Thresholds)
- **Encryption matters** — encrypted vs non-encrypted objects follow different code paths (AES padding, key management); always specify
- **Fault injection tests** are valuable but mark them clearly with `Standalone` and `FI` tags, as they modify cluster state
- **Never suggest tests for trivial code** (getters, setters, logging-only methods) — focus on business logic and error handling
- **Never hardcode credentials or IPs** — use variables from `runner.py` flags or `config.py`
- **Python 2/3 compatibility** — if the suggestion includes new Python keywords, avoid f-strings and Python 3-only syntax
- **Prioritize partially-covered methods** — in practice, the biggest coverage gains come from exercising uncovered branches within methods that are already partially hit; use `uncovered_blocks` and `partially_covered_methods` to identify these
