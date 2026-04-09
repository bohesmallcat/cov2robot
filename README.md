# coverage-robot

Automated test coverage analysis and Robot Framework test generation for Java projects.

**coverage-robot** parses JaCoCo XML and LCOV HTML coverage reports, identifies coverage gaps, generates Robot Framework end-to-end test cases to fill those gaps, and optionally runs an iterative loop that measures improvement over successive rounds.

## Features

- **Multi-format parsing** -- JaCoCo XML, JaCoCo HTML, and LCOV HTML reports; accepts local files and HTTP/HTTPS URLs
- **AI-assisted test generation** -- produces Robot Framework `.robot` files targeting uncovered code paths
- **Iterative coverage loop** -- execute tests, collect coverage, analyze gaps, generate new tests, repeat
- **Jenkins integration** -- trigger Jenkins pipeline builds, collect coverage via JaCoCo plugin API, or use pre-existing build URLs
- **PR-targeted analysis** -- focus on classes changed in a pull request (GitHub Enterprise API or local `git diff`)
- **Diff reporting** -- compare coverage between rounds with delta metrics and stop/continue recommendations
- **No vendor lock-in** -- works with any Java project that produces JaCoCo reports
- **Python 3.6+** compatible (Python 2.7 compatibility layer included for cluster-side scripts)

## Project Structure

```
coverage-robot/
├── README.md
├── LICENSE                       # Apache 2.0
├── CONTRIBUTING.md
├── coverage_parser/
│   ├── parse_coverage.py         # JaCoCo/LCOV parser (JSON output)
│   ├── check_methods.py          # Method inspection utility
│   ├── SKILL.md                  # Detailed usage documentation
│   ├── demo/                     # Demo script and sample data
│   └── references/               # Robot Framework patterns reference
├── coverage_loop/
│   ├── coverage_loop.py          # Main iterative loop orchestrator
│   ├── collect_coverage.py       # JaCoCo coverage collector (SSH/HTTP)
│   ├── diff_coverage.py          # Round-over-round diff analysis
│   ├── pr_diff.py                # PR-targeted coverage filtering
│   ├── config_template.yaml      # Configuration template
│   ├── compat/                   # Python 2 compatibility layer
│   └── SKILL.md                  # Detailed usage documentation
├── docs/                         # Additional documentation
└── examples/                     # Usage examples
```

## Quick Start

### Prerequisites

- Python 3.6+
- `paramiko` (for SSH-based coverage collection)
- `requests` (optional, for Jenkins integration)
- A Java project with JaCoCo coverage reports

```bash
pip install paramiko requests
```

### 1. Parse a Coverage Report

```bash
# JaCoCo XML (auto-detected)
python coverage_parser/parse_coverage.py path/to/jacoco.xml

# JaCoCo HTML (single-class page)
python coverage_parser/parse_coverage.py path/to/MyClass.java.html

# LCOV HTML
python coverage_parser/parse_coverage.py path/to/lcov-report.html --format lcov-html

# From a URL
python coverage_parser/parse_coverage.py https://ci.example.com/reports/jacoco.xml
```

Output is JSON to stdout:

```json
{
  "file": "com/example/MyClass.java",
  "summary": {
    "lines_hit": 39,
    "lines_total": 104,
    "line_coverage": 37.5,
    "branches_hit": 10,
    "branches_total": 50,
    "branch_coverage": 20.0
  },
  "uncovered_methods": [ "..." ],
  "partially_covered_methods": [ "..." ],
  "uncovered_blocks": [ "..." ]
}
```

### 2. Run the Iterative Coverage Loop

```bash
# Copy and edit the configuration template
cp coverage_loop/config_template.yaml config.yaml
# Edit config.yaml with your cluster details (see Configuration below)

# Single iteration
python coverage_loop/coverage_loop.py --config config.yaml --max-iterations 1

# Full loop (runs until target coverage or diminishing returns)
python coverage_loop/coverage_loop.py --config config.yaml

# Analyze existing coverage only (skip test execution)
python coverage_loop/coverage_loop.py --config config.yaml --skip-execute

# Resume from a specific round
python coverage_loop/coverage_loop.py --config config.yaml --start-round 3

# Use a pre-existing Jenkins build for coverage (skips test execution)
python coverage_loop/coverage_loop.py --config config.yaml \
    --jenkins-build https://jenkins.example.com/job/test-qe/job/my-cc/119/

# PR-targeted analysis
python coverage_loop/coverage_loop.py --config config.yaml \
    --pr-url https://github.example.com/org/repo/pull/123
```

### 3. Standalone Utilities

```bash
# Collect coverage from a live cluster
python coverage_loop/collect_coverage.py --config config.yaml --round 1

# Compare two rounds of coverage
python coverage_loop/diff_coverage.py \
    coverage-data/round_1/Block_coverage.json \
    coverage-data/round_2/Block_coverage.json

# Extract changed classes from a PR
python coverage_loop/pr_diff.py \
    --pr-url https://github.example.com/org/repo/pull/123

# Or from a local git diff
python coverage_loop/pr_diff.py \
    --repo /path/to/repo --base origin/master --head origin/feature
```

## How the Loop Works

```
Phase 1: EXECUTE    Run Robot Framework E2E tests (local or via Jenkins pipeline)
       |
       v
Phase 2: COLLECT    SSH to each node -> JaCoCo dump -> merge -> report -> pull locally
       |            (or Jenkins fast-path: fetch coverage via JaCoCo plugin API)
       v
Phase 3: ANALYZE    Parse coverage reports -> identify gaps -> diff against previous round
       |
       v
Phase 4: GENERATE   Produce new Robot Framework .robot files targeting uncovered paths
       |
       v
Phase 5: DECIDE     Target reached? Diminishing returns? Max iterations?
       |             If continue -> back to Phase 1 with the new test suite
       v
       DONE
```

**Termination conditions:**

| Condition | Default |
|-----------|---------|
| Coverage target reached | 60% line coverage |
| Diminishing returns | < 2% delta for 2 consecutive rounds |
| Maximum iterations | 5 rounds |
| No new test cases generated | 0 scenarios in Phase 4 |
| Only trivial gaps remain | All remaining gaps are P3 (accessors, logging) |

## Configuration

Copy `coverage_loop/config_template.yaml` to `config.yaml` and edit:

```yaml
cluster:
  ip: "10.x.x.x"                     # Cluster data node IP
  username: "admin"                   # SSH username
  password: ""                        # SSH password (leave empty to prompt)
  nodes: []                           # Auto-discovered if empty

target:
  service: "cm"                       # Java service to instrument
  jacoco_port: 2025                   # JaCoCo TCP port
  includes: "com.example.storage.*"   # JaCoCo class filter

execution:
  automation_dir: "/path/to/automation"
  initial_suites:
    - "robot/object/blocklayer/block_read"
  exclude_tags: ["FI", "Standalone"]

analysis:
  target_classes:
    - "Block.java"
    - "AbstractBlockData.java"

loop:
  max_iterations: 5
  target_coverage_pct: 60.0
  min_delta_pct: 2.0

# Optional: only analyze classes changed in a PR
pr_filtering:
  pr_url: ""                          # GitHub Enterprise PR URL
  local_repo_path: ""                 # Fallback: local git clone
  base_ref: "origin/master"
```

See [`coverage_loop/config_template.yaml`](coverage_loop/config_template.yaml) for the full reference with all options documented.

### Jenkins Integration

To execute tests via Jenkins instead of locally:

```yaml
execution:
  use_jenkins: true
  jenkins:
    base_url: "https://jenkins.example.com"
    job_path: "test-qe/my-component-test"
    branch: "master"
    profile: "large"
    poll_interval: 60          # seconds between status polls
    build_timeout: 7200        # max wait time (seconds)
    enable_coverage: true
    extra_params: {}           # additional Jenkins parameters
```

Jenkins credentials are loaded from environment variables or a `.env` file:

```bash
JENKINS_USERNAME=your-username
JENKINS_API_TOKEN=your-api-token
```

You can also skip test execution entirely and use a pre-existing Jenkins build:

```bash
python coverage_loop/coverage_loop.py --config config.yaml \
    --jenkins-build https://jenkins.example.com/job/test-qe/job/my-cc/119/
```

This fetches coverage directly from the Jenkins JaCoCo plugin API (Phases 2+3 combined), then continues with gap analysis and test generation (Phases 4+5).

## Coverage Gap Prioritization

The parser classifies gaps by severity:

| Priority | Gap Type | Description |
|----------|----------|-------------|
| **P0** | Uncovered method | Entire method with 0% coverage containing business logic |
| **P0-P1** | Large uncovered block | Significant uncovered region inside a partially-covered method |
| **P1** | Error/exception branch | Uncovered `catch`/`throw`/error-handling paths |
| **P2** | Small uncovered branch | Minor gaps within well-covered methods (> 80%) |
| **P3** | Trivial code | Accessors, `toString`, logging-only methods |

## Output

Each iteration produces structured output:

```
coverage-data/
├── round_1/
│   ├── html/                      # JaCoCo HTML report
│   ├── Block_coverage.json        # Parsed coverage data
│   └── round_summary.json         # Round metrics
└── round_2/
    ├── html/
    ├── Block_coverage.json
    ├── diff_from_round_1.json     # Delta from previous round
    └── round_summary.json

generated/
├── round_1/
│   ├── coverage_*.robot           # Generated test cases
│   └── generation_report.md       # Analysis report
└── round_2/
    └── ...
```

## Detailed Documentation

Each component includes its own detailed documentation:

- [`coverage_parser/SKILL.md`](coverage_parser/SKILL.md) -- Parser workflow, output format, and Robot Framework generation guide
- [`coverage_loop/SKILL.md`](coverage_loop/SKILL.md) -- Loop phases, prerequisites, integration notes, and full configuration reference
- [`coverage_parser/references/robot_patterns.md`](coverage_parser/references/robot_patterns.md) -- Robot Framework keyword conventions and test patterns

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
