---
name: coverage-loop
description: Iterative coverage-driven test generation loop for storage cluster. Executes Robot Framework E2E tests against a live storage cluster, collects JaCoCo coverage, analyzes gaps with coverage-to-robot, generates new test cases, and repeats until coverage targets are met. Use when the user wants to run an automated coverage improvement cycle, iterative test generation, or coverage-driven testing loop.
---

# Coverage Loop: Iterative Coverage-Driven Test Generation

Automated loop that repeatedly: (1) runs Robot E2E tests, (2) collects JaCoCo coverage from cluster nodes, (3) analyzes gaps, (4) generates new Robot test cases, and (5) repeats until coverage targets are met or diminishing returns are detected.

## Prerequisites

### 1. WSL / Local Environment

```bash
cd /mnt/c/Users/Danna_C/workspace/automation
pip install -r devkit/requirements.txt
python3 -c "import paramiko; import robot; print('OK')"
```

### 2. storage cluster Cluster with JaCoCo Agent Running

The target cluster service must already have the JaCoCo agent attached. Use the existing `jacoco_keywords.py` to set this up:

| Step | Action | Notes |
|------|--------|-------|
| 1 | `DOWNLOAD JACOCO JAR TO LIB` | Downloads JaCoCo 0.8.10 to all nodes |
| 2 | `Set Module Name` with service name | e.g., `cm` -> port 2025 |
| 3 | `Insert Jacoco Agent` | Patches `/opt/storage/bin/<service>` |
| 4 | `KILL MODULE PROGRESS` | Restarts service with JaCoCo agent |

**Service-to-Port Mapping** (from `jacoco_keywords.py`):

| Service | Port | Notes |
|---------|------|-------|
| ssm | 2024 | Storage Service Manager |
| cm | 2025 | Consistency Manager |
| others | 2026 | Default port for unlisted services |

The newer coverage framework (`javacoveragecollector.py`) uses ports 6300-6304. Either mapping works; configure the port in `config.yaml`.

### 3. Configuration File

Copy and edit the config template:

```bash
cp <skill-dir>/scripts/config_template.yaml config.yaml
# Edit config.yaml with your cluster details
```

### 4. Inventory Repo (for runner.py)

The automation framework expects the inventory repo at `../../inventory` relative to `automation/`. Ensure it exists or provide the cluster IP directly.

## Workflow

### Quick Start

```bash
# One-shot: run a single iteration
python3 <skill-dir>/scripts/coverage_loop.py --config config.yaml --max-iterations 1

# Full loop: run until target or max iterations
python3 <skill-dir>/scripts/coverage_loop.py --config config.yaml

# Skip execution (analyze existing coverage only)
python3 <skill-dir>/scripts/coverage_loop.py --config config.yaml --skip-execute

# Collect coverage only (no analysis or generation)
python3 <skill-dir>/scripts/collect_coverage.py --config config.yaml --round 1

# Diff two rounds
python3 <skill-dir>/scripts/diff_coverage.py \
    coverage-data/round_1/Block_coverage.json \
    coverage-data/round_2/Block_coverage.json
```

### Loop Phases

```
Phase 1: EXECUTE
  python3 runner.py --robot-path <suite> --output-dir report/round_N
     |
     v
Phase 2: COLLECT
  SSH to each node -> jacococli dump -> merge -> generate HTML -> SCP to local
     |
     v
Phase 3: ANALYZE
  parse_coverage.py on each target class HTML -> coverage JSON
  diff_coverage.py comparing round_N vs round_(N-1)
     |
     v
Phase 4: GENERATE
  coverage-to-robot skill -> new .robot files in generated/round_N/
     |
     v
Phase 5: DECIDE
  Check: target reached? diminishing returns? max iterations?
  If continue -> back to Phase 1 with new suite files
```

### Output Structure

```
coverage-robot/
├── coverage-data/
│   ├── round_1/
│   │   ├── html/                  # JaCoCo HTML report
│   │   ├── Block_coverage.json    # Parsed coverage per class
│   │   └── round_summary.json     # Round summary with metrics
│   └── round_2/
│       ├── html/
│       ├── Block_coverage.json
│       ├── diff_from_round_1.json # Delta metrics
│       └── round_summary.json
├── generated/
│   ├── round_1/
│   │   ├── coverage_*.robot       # Generated test cases
│   │   └── generation_report.md
│   └── round_2/
│       └── ...
└── config.yaml
```

## Configuration Reference

```yaml
cluster:
  ip: "10.x.x.x"                    # storage cluster public IP (any data node)
  username: "admin"                  # SSH username
  password: ""                       # SSH password
  nodes: []                          # Optional: explicit node list (auto-discovered if empty)

target:
  service: "cm"                      # cluster service to collect coverage from
  jacoco_port: 2025                  # JaCoCo TCP port
  source_code_path: ""               # Path to Java source on cluster (for report gen)
  class_files_path: ""               # Path to compiled JARs/classes on cluster
  includes: "com.example.storage.*"    # JaCoCo includes filter

execution:
  automation_dir: "/mnt/c/Users/Danna_C/workspace/automation"
  initial_suites:
    - "robot/object/blocklayer/block_read"
  include_tags: []
  exclude_tags: ["FI", "Standalone"]
  runner_profile: "small"
  cluster_name: ""                   # Cluster name for runner.py --cluster flag
  environment: "dev"

analysis:
  target_classes:                    # Java class HTML files to parse
    - "Block.java"
    - "AbstractBlockData.java"
  coverage_to_robot_script: "../skills/coverage-to-robot/scripts/parse_coverage.py"

loop:
  max_iterations: 5
  target_coverage_pct: 60.0
  min_delta_pct: 2.0                 # Stop if < this for 2 consecutive rounds
  skip_priority: ["P3"]

output:
  generated_dir: "generated"
  coverage_data_dir: "coverage-data"
```

## Scripts

| Script | Purpose |
|--------|---------|
| `coverage_loop.py` | Main orchestrator — runs the full loop |
| `collect_coverage.py` | SSH-based JaCoCo dump, merge, report gen, SCP pull |
| `diff_coverage.py` | Compare two rounds of coverage JSON, output delta metrics |
| `config_template.yaml` | Default configuration template |

## Integration with Existing Framework

This skill builds on:

- **`coverage-to-robot`** — Parses JaCoCo HTML/XML and generates Robot test case suggestions
- **`jacoco_keywords.py`** — Robot keywords for JaCoCo agent setup (instrument, dump, merge, report)
- **`CodeCoverageListener`** — Robot listener that auto-dumps JaCoCo per test (used in Jenkins CI)
- **`javacoveragecollector.py`** — Newer coverage collector framework with factory pattern

The loop orchestrator directly calls the automation `runner.py` and reuses SSH patterns from `jacoco_keywords.py`, but operates standalone (no Robot Framework dependency for the loop itself).

## Termination Conditions

| Condition | Default Threshold |
|-----------|-------------------|
| Coverage target reached | 60% line coverage |
| Diminishing returns | delta < 2% for 2 consecutive rounds |
| Max iterations | 5 rounds |
| No new cases generated | Phase 4 produces 0 scenarios |
| All remaining gaps are low priority | Only P3 (trivial) gaps left |

## Important Notes

- **JaCoCo agent must be pre-attached** to the target service before running the loop. The loop does NOT instrument services.
- **Coverage accumulates** across rounds because `jacococli dump --reset` is NOT used by default (configurable).
- **Generated cases go to a review directory**, not directly into the automation repo.
- **The loop is designed for WSL local execution** — no Jenkins or Docker required.
- **Python 2/3 compatibility**: The orchestrator scripts are Python 3 only; they do NOT need to run under Python 2.
