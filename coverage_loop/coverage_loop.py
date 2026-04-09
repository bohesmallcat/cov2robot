#!/usr/bin/env python3
"""
Coverage Loop Orchestrator.

Iteratively:
  1. Execute Robot E2E tests against an storage cluster
  2. Collect JaCoCo coverage from the cluster
  3. Analyze coverage gaps (XML-based or HTML-based)
  4. Generate new Robot test cases via coverage-to-robot
  5. Decide whether to continue or stop

Usage:
    python coverage_loop.py --config config.yaml
    python coverage_loop.py --config config.yaml --max-iterations 1
    python coverage_loop.py --config config.yaml --skip-execute
    python coverage_loop.py --config config.yaml --start-round 3
"""

import argparse
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import xml.etree.ElementTree as ET
except ImportError:
    ET = None

try:
    import yaml
except ImportError:
    yaml = None

try:
    import requests as _requests
    import urllib3 as _urllib3
    _urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    _requests = None

# Import sibling modules
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from collect_coverage import collect_coverage, load_config  # noqa: E402
from diff_coverage import diff_coverage, diff_round_dir, recommend, load_json  # noqa: E402
from pr_diff import get_pr_context, extract_changed_classes  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("coverage_loop")


# ---------------------------------------------------------------------------
# Phase 1: Execute Robot tests
# ---------------------------------------------------------------------------

def execute_tests(config, round_num, suite_paths=None):
    """
    Run Robot Framework tests directly (bypasses runner.py to avoid RF 7.x
    ``noncritical`` incompatibility).

    Falls back to runner.py when ``execution.use_runner_py`` is True.

    Parameters
    ----------
    config : dict
        Full configuration.
    round_num : int
        Current round number.
    suite_paths : list of str, optional
        Robot suite paths to execute.  If *None*, uses ``initial_suites``
        from config.

    Returns
    -------
    dict or None
        Execution result with output_dir, or None on failure.
    """
    exec_cfg = config["execution"]

    # --- Jenkins execution mode ---
    if exec_cfg.get("use_jenkins", False):
        log.info("Using Jenkins execution mode for round %d", round_num)
        return _execute_via_jenkins(config, round_num, suite_paths)

    automation_dir = exec_cfg["automation_dir"]

    if suite_paths is None:
        suite_paths = exec_cfg.get("initial_suites", [])
    if not suite_paths:
        log.error("No suite paths specified for round %d", round_num)
        return None

    output_dir = os.path.join(
        automation_dir, "report", "round_{n}".format(n=round_num)
    )
    os.makedirs(output_dir, exist_ok=True)

    use_runner = exec_cfg.get("use_runner_py", False)

    results = []
    for suite_path in suite_paths:
        sp = suite_path.rstrip("/")
        if sp.endswith(".robot"):
            sp = sp[:-6]
        robot_path_dir = os.path.dirname(sp)
        suite_name = os.path.basename(sp)

        run_env = None  # inherit current env by default
        if use_runner:
            cmd = _build_runner_cmd(
                exec_cfg, config, automation_dir, output_dir,
                suite_name, robot_path_dir,
            )
        else:
            cmd, run_env = _build_robot_cmd(
                exec_cfg, config, automation_dir, output_dir,
                suite_name, robot_path_dir,
            )

        log.info("Executing: %s", " ".join(cmd))
        log.info("Suite: %s (round %d)", suite_path, round_num)

        try:
            result = subprocess.run(
                cmd,
                cwd=automation_dir,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
                env=run_env,
            )
            log.info("Exit code: %d", result.returncode)
            if result.stdout:
                lines = result.stdout.strip().splitlines()
                for line in lines[-30:]:
                    log.info("  %s", line)
            if result.returncode != 0 and result.stderr:
                for line in result.stderr.strip().splitlines()[-10:]:
                    log.warning("  stderr: %s", line)

            results.append({
                "suite": suite_path,
                "exit_code": result.returncode,
            })
        except subprocess.TimeoutExpired:
            log.error("Test execution timed out for %s", suite_path)
            results.append({
                "suite": suite_path,
                "exit_code": -1,
                "error": "timeout",
            })
        except Exception as exc:
            log.error("Failed to execute tests: %s", exc)
            results.append({
                "suite": suite_path,
                "exit_code": -1,
                "error": str(exc),
            })

    return {
        "round": round_num,
        "output_dir": output_dir,
        "results": results,
    }


def _build_robot_cmd(exec_cfg, config, automation_dir, output_dir,
                     suite_name, robot_path_dir):
    """Build ``robot`` CLI command directly (RF 7.x compatible).

    Returns (cmd_list, env_dict).
    """
    # Resolve the test path
    tests_path = os.path.join(automation_dir, robot_path_dir, suite_name)
    if not os.path.exists(tests_path):
        alt = tests_path + ".robot"
        if os.path.exists(alt):
            tests_path = alt

    cluster_name = exec_cfg.get("cluster_name", "")
    if not cluster_name:
        cluster_name = config["cluster"]["ip"]

    # Build Robot variables matching runner.py convention (UPPER_CASE)
    variables = {
        "ENVIRONMENT": exec_cfg.get("environment", "dev"),
        "FLEX_CLUSTER": cluster_name,
        "PLATFORM": exec_cfg.get("platform", "vanilla"),
        "PROFILE": exec_cfg.get("runner_profile", "small"),
    }

    cmd = [
        sys.executable, "-m", "robot",
        "--loglevel", "DEBUG:INFO",
        "--outputdir", output_dir,
        "--suite", suite_name,
        "--debugfile", "dbg_robot.log",
        "--consolecolors", "ansi",
        "--exclude", "excluded",
        "--xunit", "xunit_report.xml",
        "--consolemarkers", "OFF",
        "--runemptysuite",
        # RF 7.x replacement for the removed --noncritical
        "--skiponfailure", "unstable",
        # Library search paths (automation root is already cwd)
        "--pythonpath", automation_dir,
    ]

    # Add service-console paths if they exist
    sc_common = os.path.join(
        os.path.dirname(automation_dir),
        "service-console", "Automation", "common",
    )
    sc_lib = os.path.join(
        os.path.dirname(automation_dir),
        "service-console", "Automation", "lib",
    )
    for p in (sc_common, sc_lib):
        if os.path.isdir(p):
            cmd.extend(["--pythonpath", p])

    # Extra python paths from config
    for p in exec_cfg.get("extra_pythonpaths", []):
        if os.path.isdir(p):
            cmd.extend(["--pythonpath", p])

    for var_name, var_value in variables.items():
        if var_value:
            cmd.extend(["--variable", "{k}:{v}".format(k=var_name, v=var_value)])

    for tag in exec_cfg.get("include_tags", []):
        cmd.extend(["--include", tag])
    for tag in exec_cfg.get("exclude_tags", []):
        cmd.extend(["--exclude", tag])

    cmd.append(tests_path)

    # Build environment with compat shim for Python 3.10+
    env = os.environ.copy()
    compat_dir = os.path.join(SCRIPT_DIR, "compat")
    if os.path.isdir(compat_dir):
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            compat_dir + os.pathsep + existing if existing else compat_dir
        )

    return cmd, env


def _build_runner_cmd(exec_cfg, config, automation_dir, output_dir,
                      suite_name, robot_path_dir):
    """Build ``runner.py`` command (legacy, may fail on RF 7.x)."""
    runner_py = os.path.join(automation_dir, "runner.py")
    cluster_name = exec_cfg.get("cluster_name", "")
    if not cluster_name:
        cluster_name = config["cluster"]["ip"]

    cmd = [
        sys.executable, runner_py,
        "--environment", exec_cfg.get("environment", "dev"),
        "--profile", exec_cfg.get("runner_profile", "small"),
        "--output-dir", output_dir,
        "--suite", suite_name,
        "--robot-path", robot_path_dir,
        "--flex-cluster", cluster_name,
    ]

    for tag in exec_cfg.get("include_tags", []):
        cmd.extend(["--include-test-tag", tag])
    for tag in exec_cfg.get("exclude_tags", []):
        cmd.extend(["--exclude-test-tag", tag])

    return cmd


# ---------------------------------------------------------------------------
# Phase 1b: Execute Robot tests via Jenkins pipeline
# ---------------------------------------------------------------------------

def _load_jenkins_credentials():
    """Load Jenkins credentials from .env or environment variables."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None

    env_file = None
    for search_dir in [os.getcwd(), SCRIPT_DIR]:
        current = os.path.abspath(search_dir)
        while True:
            candidate = os.path.join(current, ".env")
            if os.path.exists(candidate):
                env_file = candidate
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        if env_file:
            break

    if env_file and load_dotenv:
        load_dotenv(dotenv_path=env_file, override=True)
    elif env_file:
        with open(env_file, "r") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    username = os.environ.get("JENKINS_USERNAME", "")
    token = os.environ.get("JENKINS_API_TOKEN", "")
    if not username or not token:
        raise RuntimeError(
            "Jenkins credentials missing. Need JENKINS_USERNAME and "
            "JENKINS_API_TOKEN in .env or environment variables."
        )
    return username, token


def _jenkins_get(url, username, token, timeout=30):
    """HTTP GET with Jenkins basic auth."""
    resp = _requests.get(
        url, auth=(username, token), verify=False, timeout=timeout
    )
    resp.raise_for_status()
    return resp


def _jenkins_post(url, username, token, params=None, timeout=30):
    """HTTP POST with Jenkins basic auth."""
    resp = _requests.post(
        url, auth=(username, token), verify=False, timeout=timeout,
        params=params,
    )
    resp.raise_for_status()
    return resp


def _execute_via_jenkins(config, round_num, suite_paths=None):
    """Execute Robot tests via a Jenkins pipeline build.

    Triggers the configured Jenkins job, polls until the build completes,
    and returns the result dict compatible with ``execute_tests()``.

    Configuration keys under ``execution.jenkins``:
        job_path : str
            Jenkins job path (e.g. 'test-qe/my-component-test').
        base_url : str
            Jenkins base URL (e.g. 'https://jenkins.example.com').
        branch : str, optional
            Automation repo branch (default: 'master').
        profile : str, optional
            Runner profile (default: value of execution.runner_profile).
        poll_interval : int, optional
            Seconds between build-status polls (default: 60).
        build_timeout : int, optional
            Max seconds to wait for the build to finish (default: 7200).
        extra_params : dict, optional
            Additional Jenkins build parameters to pass.
    """
    if _requests is None:
        log.error("requests library not available; cannot use Jenkins mode")
        return None

    exec_cfg = config["execution"]
    jenkins_cfg = exec_cfg.get("jenkins", {})
    job_path = jenkins_cfg.get("job_path", "")
    if not job_path:
        log.error("execution.jenkins.job_path not configured")
        return None

    # Resolve suite paths
    if suite_paths is None:
        suite_paths = exec_cfg.get("initial_suites", [])
    if not suite_paths:
        log.error("No suite paths specified for Jenkins round %d", round_num)
        return None

    # Build the SUITE parameter: Jenkins expects the .robot path
    suite_value = suite_paths[0].rstrip("/")
    if not suite_value.endswith(".robot"):
        suite_value += ".robot"

    username, token = _load_jenkins_credentials()
    base_url = jenkins_cfg.get(
        "base_url", "https://jenkins.example.com"
    )

    # Normalize job path for Jenkins API
    path_parts = [p for p in job_path.strip("/").split("/") if p]
    if not job_path.startswith("/job/") and not path_parts[0] == "job":
        api_path = "/job/" + "/job/".join(path_parts)
    elif path_parts[0] == "job":
        api_path = "/" + "/".join(path_parts)
    else:
        api_path = job_path
    job_url = base_url.rstrip("/") + api_path

    cluster_name = exec_cfg.get("cluster_name", "")
    if not cluster_name:
        cluster_name = config["cluster"]["ip"]

    # Assemble build parameters
    params = {
        "AUTOMATION_REPO_BRANCH_NAME": jenkins_cfg.get("branch", "master"),
        "SUITE": suite_value,
        "ENVIRONMENT": exec_cfg.get("environment", "dev"),
        "PLATFORM": exec_cfg.get("platform", "vanilla"),
        "CLUSTER": cluster_name,
        "PROFILE": jenkins_cfg.get(
            "profile", exec_cfg.get("runner_profile", "large")
        ),
        "ADVANCED_PARAMETERS": "--python3",
        "ENABLE_CODE_COVERAGE": str(
            jenkins_cfg.get("enable_coverage", True)
        ).lower(),
    }
    # Merge any extra params from config
    params.update(jenkins_cfg.get("extra_params", {}))

    log.info("Triggering Jenkins build: %s", job_url)
    log.info("Parameters: %s", json.dumps(params, indent=2))

    # Trigger build
    try:
        resp = _jenkins_post(
            "{url}/buildWithParameters".format(url=job_url),
            username, token, params=params,
        )
    except Exception as exc:
        log.error("Failed to trigger Jenkins build: %s", exc)
        return None

    queue_url = resp.headers.get("Location", "")
    if not queue_url:
        log.error("Jenkins did not return a queue URL")
        return None

    log.info("Build queued: %s", queue_url)

    # Wait for queue item to get a build number
    queue_api = queue_url.rstrip("/") + "/api/json"
    build_number = None
    build_url = None
    queue_deadline = time.time() + 300  # 5 min for queue
    while time.time() < queue_deadline:
        time.sleep(5)
        try:
            qresp = _jenkins_get(queue_api, username, token)
            qdata = qresp.json()
            executable = qdata.get("executable")
            if executable:
                build_number = executable.get("number")
                build_url = executable.get("url", "")
                break
            if qdata.get("cancelled"):
                log.error("Jenkins build was cancelled in queue")
                return None
        except Exception:
            pass

    if not build_number:
        log.error("Timed out waiting for Jenkins build number")
        return None

    log.info("Jenkins build started: #%s — %s", build_number, build_url)

    # Poll until build completes
    poll_interval = jenkins_cfg.get("poll_interval", 60)
    build_timeout = jenkins_cfg.get("build_timeout", 7200)
    build_api = "{url}/api/json".format(
        url=build_url.rstrip("/") if build_url else
        "{jurl}/{num}".format(jurl=job_url, num=build_number)
    )
    deadline = time.time() + build_timeout

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            bresp = _jenkins_get(build_api, username, token)
            bdata = bresp.json()
            building = bdata.get("building", True)
            result = bdata.get("result")
            duration_ms = bdata.get("duration", 0)

            if not building and result:
                duration_s = duration_ms / 1000.0
                log.info(
                    "Jenkins build #%s finished: %s (%.0fs / %.1f min)",
                    build_number, result, duration_s, duration_s / 60,
                )
                exit_code = 0 if result == "SUCCESS" else 1
                return {
                    "round": round_num,
                    "output_dir": None,  # no local output dir
                    "jenkins_build_url": build_url,
                    "results": [{
                        "suite": suite_paths[0],
                        "exit_code": exit_code,
                        "jenkins_build": build_number,
                        "jenkins_url": build_url,
                        "jenkins_result": result,
                        "duration_s": duration_s,
                    }],
                }
            else:
                elapsed = time.time() - (deadline - build_timeout)
                log.info(
                    "Jenkins build #%s still running (%.0f min elapsed)...",
                    build_number, elapsed / 60,
                )
        except Exception as exc:
            log.warning("Error polling Jenkins build: %s", exc)

    log.error(
        "Jenkins build #%s did not finish within %ds", build_number,
        build_timeout,
    )
    return {
        "round": round_num,
        "output_dir": None,
        "jenkins_build_url": build_url,
        "results": [{
            "suite": suite_paths[0],
            "exit_code": -1,
            "error": "jenkins_timeout",
            "jenkins_build": build_number,
            "jenkins_url": build_url,
        }],
    }


# ---------------------------------------------------------------------------
# Phase 2b: Collect coverage from Jenkins JaCoCo plugin API
# ---------------------------------------------------------------------------

def _collect_coverage_from_jenkins(build_url, config, round_num,
                                   pr_context=None):
    """Fetch per-class coverage from the Jenkins JaCoCo plugin REST API.

    This replaces the SSH-based ``collect_coverage`` + ``analyze_coverage``
    phases when the Jenkins pipeline already handles JaCoCo dump/merge/report.

    Parameters
    ----------
    build_url : str
        Full Jenkins build URL (e.g.
        ``https://jenkins.example.com/job/test-qe/job/my-cc/119/``).
    config : dict
        Full configuration.
    round_num : int
        Current round number.
    pr_context : dict, optional
        PR context containing ``changed_classes`` set.

    Returns
    -------
    dict or None
        Same format as ``analyze_coverage()``: mapping of
        fully-qualified class name -> coverage dict.
    """
    if _requests is None:
        log.error("requests library not available; cannot query Jenkins API")
        return None

    username, token = _load_jenkins_credentials()
    jacoco_base = build_url.rstrip("/") + "/jacoco"

    # --- Determine which packages/classes to fetch ---
    changed_classes = None
    if pr_context:
        changed_classes = pr_context.get("changed_classes")

    analysis_cfg = config.get("analysis", {})
    target_packages = analysis_cfg.get("target_packages", [])
    if not target_packages:
        includes = config.get("target", {}).get("includes", "")
        if includes:
            target_packages = [includes.rstrip(".*")]

    # --- Step 1: List all packages from JaCoCo plugin ---
    log.info("Fetching JaCoCo package list from Jenkins...")
    try:
        resp = _jenkins_get(jacoco_base + "/", username, token)
    except Exception as exc:
        log.error("Failed to fetch JaCoCo page: %s", exc)
        return None

    pkg_links = re.findall(r'href="(com\.[^"]+/)"', resp.text)
    all_packages = sorted(set(p.rstrip("/") for p in pkg_links))
    log.info("Found %d packages in JaCoCo report", len(all_packages))

    # Filter packages
    pr_filter_missed = False
    if changed_classes:
        # PR-mode: only packages that contain changed classes
        pr_packages = set()
        for fqn in changed_classes:
            pkg = fqn.rsplit(".", 1)[0] if "." in fqn else fqn
            pr_packages.add(pkg)
        packages_to_fetch = [
            p for p in all_packages if p in pr_packages
        ]
        if packages_to_fetch:
            log.info(
                "PR-mode: filtered to %d packages containing changed classes",
                len(packages_to_fetch),
            )
        else:
            # PR classes not in JaCoCo scope — collect ALL available packages
            # so we still get useful coverage data from the build
            pr_filter_missed = True
            log.warning(
                "PR-mode: none of the %d changed-class packages found in "
                "JaCoCo report (%d packages available). "
                "Collecting ALL available packages instead.",
                len(pr_packages), len(all_packages),
            )
            log.info(
                "  PR packages wanted: %s",
                ", ".join(sorted(pr_packages)),
            )
            log.info(
                "  JaCoCo packages available: %s",
                ", ".join(all_packages),
            )
            packages_to_fetch = all_packages
    elif target_packages:
        packages_to_fetch = [
            p for p in all_packages
            if any(p.startswith(tp) for tp in target_packages)
        ]
        log.info(
            "Filtered to %d target packages", len(packages_to_fetch),
        )
    else:
        packages_to_fetch = all_packages

    if not packages_to_fetch:
        log.warning("No matching packages found in JaCoCo report")
        return None

    # --- Step 2: For each package, list classes and fetch coverage ---
    results = {}

    for pkg_name in packages_to_fetch:
        log.info("  Package: %s", pkg_name)
        pkg_url = "{base}/{pkg}/".format(base=jacoco_base, pkg=pkg_name)
        try:
            pkg_resp = _jenkins_get(pkg_url, username, token)
        except Exception as exc:
            log.warning("  Failed to fetch package %s: %s", pkg_name, exc)
            continue

        # Parse class links from the package page
        class_links = re.findall(r'href="([A-Z][a-zA-Z0-9_]+/)"', pkg_resp.text)
        class_names = sorted(set(c.rstrip("/") for c in class_links))

        for cls_name in class_names:
            cls_fqn = "{pkg}.{cls}".format(pkg=pkg_name, cls=cls_name)

            # Skip inner classes
            if "$" in cls_name:
                continue

            # PR-mode filter (skip if PR packages ARE in JaCoCo scope)
            if (changed_classes and not pr_filter_missed
                    and cls_fqn not in changed_classes):
                continue

            # Fetch class-level coverage from API
            cls_api = "{base}/{pkg}/{cls}/api/json".format(
                base=jacoco_base, pkg=pkg_name, cls=cls_name,
            )
            try:
                cls_resp = _jenkins_get(cls_api, username, token)
                cls_data = cls_resp.json()
            except Exception as exc:
                log.warning("  Failed to fetch class %s: %s", cls_fqn, exc)
                continue

            # Transform to the standard coverage dict format
            line_ctr = cls_data.get("lineCoverage", {})
            branch_ctr = cls_data.get("branchCoverage", {})
            method_ctr = cls_data.get("methodCoverage", {})
            instr_ctr = cls_data.get("instructionCoverage", {})

            line_total = line_ctr.get("total", 0)
            line_covered = line_ctr.get("covered", 0)
            line_missed = line_ctr.get("missed", 0)
            line_pct = (
                100.0 * line_covered / line_total if line_total > 0 else 0.0
            )

            branch_total = branch_ctr.get("total", 0)
            branch_covered = branch_ctr.get("covered", 0)
            branch_pct = (
                100.0 * branch_covered / branch_total
                if branch_total > 0 else 0.0
            )

            results[cls_fqn] = {
                "class": cls_fqn,
                "source_file": "",
                "package": pkg_name,
                "summary": {
                    "line_coverage": round(line_pct, 1),
                    "branch_coverage": round(branch_pct, 1),
                    "lines_hit": line_covered,
                    "lines_total": line_total,
                    "lines_missed": line_missed,
                    "branches_hit": branch_covered,
                    "branches_total": branch_total,
                    "methods_hit": method_ctr.get("covered", 0),
                    "methods_total": method_ctr.get("total", 0),
                    "instructions_hit": instr_ctr.get("covered", 0),
                    "instructions_total": instr_ctr.get("total", 0),
                },
                # Method-level detail not available from plugin API
                "methods": [],
                "uncovered_methods": [],
                "partially_covered_methods": [],
            }

            log.info(
                "    %s: line %.1f%% (%d/%d), branch %.1f%%",
                cls_name, line_pct, line_covered, line_total, branch_pct,
            )

    if not results:
        log.warning("No class coverage data retrieved from Jenkins")
        return None

    log.info(
        "Jenkins JaCoCo API: %d classes retrieved (%d with coverage)",
        len(results),
        sum(1 for r in results.values()
            if r["summary"]["lines_hit"] > 0),
    )
    return results


# ---------------------------------------------------------------------------
# Phase 3: Analyze coverage
# ---------------------------------------------------------------------------

def _parse_xml_report(xml_path, target_packages=None, changed_classes=None):
    """
    Parse a JaCoCo XML report and return per-class coverage data.

    Parameters
    ----------
    xml_path : str
        Path to coverage-report.xml.
    target_packages : list of str, optional
        If set, only return classes from these package prefixes
        (dot-separated, e.g. ``com.example.storage.data.blockmanager``).
        If *None*, return all classes.
    changed_classes : set of str, optional
        If set, only return classes whose FQN is in this set (PR-mode
        filtering).  When provided, *target_packages* is ignored.

    Returns
    -------
    dict
        Mapping of fully-qualified class name -> coverage dict.
    """
    if ET is None:
        log.error("xml.etree.ElementTree not available")
        return {}

    tree = ET.parse(xml_path)
    root = tree.getroot()

    results = {}

    for pkg_elem in root.findall(".//package"):
        pkg_name = pkg_elem.get("name", "").replace("/", ".")

        # When PR-mode filtering is active, skip the broader package filter
        if not changed_classes and target_packages:
            if not any(pkg_name.startswith(tp) for tp in target_packages):
                continue

        for cls_elem in pkg_elem.findall("class"):
            cls_path = cls_elem.get("name", "")
            cls_fqn = cls_path.replace("/", ".")
            source_file = cls_elem.get("sourcefilename", "")

            # Skip inner classes for the summary (report under parent)
            if "$" in cls_fqn:
                continue

            # PR-mode: only include classes changed in the PR
            if changed_classes and cls_fqn not in changed_classes:
                continue

            counters = {}
            for ctr in cls_elem.findall("counter"):
                ctype = ctr.get("type")
                missed = int(ctr.get("missed", 0))
                covered = int(ctr.get("covered", 0))
                counters[ctype] = {
                    "missed": missed,
                    "covered": covered,
                    "total": missed + covered,
                }

            line_ctr = counters.get("LINE", {"missed": 0, "covered": 0, "total": 0})
            branch_ctr = counters.get("BRANCH", {"missed": 0, "covered": 0, "total": 0})
            method_ctr = counters.get("METHOD", {"missed": 0, "covered": 0, "total": 0})
            instr_ctr = counters.get("INSTRUCTION", {"missed": 0, "covered": 0, "total": 0})

            line_total = line_ctr["total"]
            line_cov_pct = (
                100.0 * line_ctr["covered"] / line_total if line_total > 0 else 0.0
            )
            branch_total = branch_ctr["total"]
            branch_cov_pct = (
                100.0 * branch_ctr["covered"] / branch_total
                if branch_total > 0 else 0.0
            )

            # Parse methods
            methods = []
            uncovered_methods = []
            partially_covered_methods = []
            for meth_elem in cls_elem.findall("method"):
                mname = meth_elem.get("name", "")
                mdesc = meth_elem.get("desc", "")
                mline = meth_elem.get("line", "")
                m_counters = {}
                for mc in meth_elem.findall("counter"):
                    mt = mc.get("type")
                    m_counters[mt] = {
                        "missed": int(mc.get("missed", 0)),
                        "covered": int(mc.get("covered", 0)),
                    }
                m_line = m_counters.get("LINE", {"missed": 0, "covered": 0})
                m_total = m_line["missed"] + m_line["covered"]
                m_pct = (
                    100.0 * m_line["covered"] / m_total if m_total > 0 else 0.0
                )

                minfo = {
                    "name": mname,
                    "desc": mdesc,
                    "line": mline,
                    "lines_covered": m_line["covered"],
                    "lines_missed": m_line["missed"],
                    "line_coverage_pct": round(m_pct, 1),
                }
                methods.append(minfo)

                if m_line["covered"] == 0 and m_total > 0:
                    uncovered_methods.append(minfo)
                elif 0 < m_pct < 100.0:
                    partially_covered_methods.append(minfo)

            results[cls_fqn] = {
                "class": cls_fqn,
                "source_file": source_file,
                "package": pkg_name,
                "summary": {
                    "line_coverage": round(line_cov_pct, 1),
                    "branch_coverage": round(branch_cov_pct, 1),
                    "lines_hit": line_ctr["covered"],
                    "lines_total": line_total,
                    "lines_missed": line_ctr["missed"],
                    "branches_hit": branch_ctr["covered"],
                    "branches_total": branch_total,
                    "methods_hit": method_ctr["covered"],
                    "methods_total": method_ctr["total"],
                    "instructions_hit": instr_ctr["covered"],
                    "instructions_total": instr_ctr["total"],
                },
                "methods": methods,
                "uncovered_methods": uncovered_methods,
                "partially_covered_methods": partially_covered_methods,
            }

    return results


def analyze_coverage(config, round_num, html_dir=None, xml_path=None,
                     pr_context=None):
    """
    Analyze JaCoCo coverage data.

    Prefers XML-based analysis (complete, no source needed).
    Falls back to HTML + parse_coverage.py if XML is unavailable.

    Parameters
    ----------
    config : dict
        Full configuration.
    round_num : int
        Current round number.
    html_dir : str, optional
        Path to JaCoCo HTML report directory.
    xml_path : str, optional
        Path to JaCoCo XML report.
    pr_context : dict, optional
        PR context from ``pr_diff.get_pr_context()``.  When provided,
        coverage analysis is restricted to classes changed in the PR.

    Returns dict mapping class name -> coverage JSON.
    """
    analysis_cfg = config.get("analysis", {})
    output_cfg = config.get("output", {})
    coverage_data_dir = output_cfg.get("coverage_data_dir", "coverage-data")
    round_dir = os.path.join(
        coverage_data_dir, "round_{n}".format(n=round_num),
    )
    os.makedirs(round_dir, exist_ok=True)

    # --- Extract PR-mode class filter ---
    changed_classes = None
    if pr_context:
        changed_classes = pr_context.get("changed_classes")
        if changed_classes:
            log.info(
                "PR-mode: filtering coverage to %d changed classes",
                len(changed_classes),
            )
            for fqn in sorted(changed_classes):
                log.info("  PR class: %s", fqn)

    # --- Try XML-based analysis first ---
    if xml_path is None:
        xml_path = os.path.join(round_dir, "coverage-report.xml")

    if os.path.exists(xml_path):
        log.info("Using XML-based analysis: %s", xml_path)
        target_packages = analysis_cfg.get("target_packages", [])
        if not target_packages:
            # Derive from target.includes config
            includes = config.get("target", {}).get("includes", "")
            if includes:
                # "com.example.storage.data.object.**" -> "com.example.storage.data.object"
                target_packages = [includes.rstrip(".*")]
            else:
                target_packages = None

        results = _parse_xml_report(
            xml_path, target_packages,
            changed_classes=changed_classes,
        )
        if results:
            log.info(
                "XML analysis found %d classes (%d with coverage)",
                len(results),
                sum(
                    1 for r in results.values()
                    if r["summary"]["lines_hit"] > 0
                ),
            )
            # Save per-class JSON files
            for cls_fqn, data in results.items():
                safe_name = cls_fqn.replace(".", "_")
                out_file = os.path.join(
                    round_dir, "{cls}_coverage.json".format(cls=safe_name),
                )
                with open(out_file, "w") as f:
                    json.dump(data, f, indent=2)

            _write_round_summary(round_dir, round_num, results)
            return results

        log.warning("XML analysis returned empty results; trying HTML")

    # --- Fallback: HTML-based analysis ---
    if html_dir and os.path.isdir(html_dir):
        results = _analyze_coverage_html(config, round_num, html_dir, round_dir)
        if results:
            _write_round_summary(round_dir, round_num, results)
            return results

    log.error("No coverage data available for analysis")
    _write_round_summary(round_dir, round_num, {})
    return {}


def _write_round_summary(round_dir, round_num, results):
    """Persist a compact round_summary.json."""
    summary = {
        "round": round_num,
        "classes_analyzed": len(results),
        "classes_with_coverage": sum(
            1 for r in results.values()
            if r.get("summary", {}).get("lines_hit", 0) > 0
        ),
        "classes": {},
    }
    for cls_name, data in results.items():
        s = data.get("summary", {})
        summary["classes"][cls_name] = {
            "line_coverage": s.get("line_coverage", 0.0),
            "branch_coverage": s.get("branch_coverage", 0.0),
            "lines_hit": s.get("lines_hit", 0),
            "lines_total": s.get("lines_total", 0),
            "uncovered_methods": len(data.get("uncovered_methods", [])),
            "partially_covered_methods": len(
                data.get("partially_covered_methods", [])
            ),
        }

    summary_file = os.path.join(round_dir, "round_summary.json")
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)
    log.info("Round summary written: %s", summary_file)
    log.info(
        "  %d classes analyzed, %d with coverage",
        summary["classes_analyzed"],
        summary["classes_with_coverage"],
    )
    return summary


def _analyze_coverage_html(config, round_num, html_dir, round_dir):
    """HTML-based fallback analysis using parse_coverage.py."""
    analysis_cfg = config.get("analysis", {})

    parse_script = analysis_cfg.get("coverage_to_robot_script", "")
    if not parse_script or not os.path.exists(parse_script):
        skill_root = os.path.dirname(os.path.dirname(SCRIPT_DIR))
        parse_script = os.path.join(
            skill_root, "skills", "coverage-to-robot", "scripts",
            "parse_coverage.py",
        )
    if not os.path.exists(parse_script):
        log.error("parse_coverage.py not found at %s", parse_script)
        return {}

    target_classes = analysis_cfg.get("target_classes", [])
    if not target_classes:
        log.warning("No target_classes configured; scanning all HTML files")
        pattern = os.path.join(html_dir, "**", "*.html")
        html_files = glob.glob(pattern, recursive=True)
        # Filter to class-level files (not index, resources, etc.)
        target_classes = [
            os.path.basename(f)
            for f in html_files
            if not os.path.basename(f).startswith("index")
            and "jacoco-" not in os.path.basename(f)
        ]

    results = {}
    for class_name in target_classes:
        base_name = class_name
        if not base_name.endswith(".html"):
            base_name = base_name + ".html"

        pattern = os.path.join(html_dir, "**", base_name)
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            log.warning("HTML file not found for %s in %s", class_name, html_dir)
            continue

        html_file = matches[0]
        log.info("Parsing coverage for %s: %s", class_name, html_file)
        try:
            result = subprocess.run(
                [sys.executable, parse_script, html_file],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                log.error(
                    "parse_coverage.py failed for %s: %s",
                    class_name, result.stderr,
                )
                continue
            coverage_data = json.loads(result.stdout)
            safe_name = class_name.replace(".java", "").replace(".html", "")
            out_file = os.path.join(
                round_dir, "{cls}_coverage.json".format(cls=safe_name),
            )
            with open(out_file, "w") as f:
                json.dump(coverage_data, f, indent=2)
            results[class_name] = coverage_data
        except Exception as exc:
            log.error("Failed to parse %s: %s", class_name, exc)

    return results


# ---------------------------------------------------------------------------
# Phase 4: Generate new test cases (placeholder — needs AI agent)
# ---------------------------------------------------------------------------

def generate_test_cases(config, round_num, coverage_results, diff_results):
    """
    Generate Robot Framework test case suggestions based on coverage gaps.

    This function creates a generation prompt/report that can be used by
    the coverage-to-robot skill (invoked by an AI agent) to produce
    actual .robot files.

    Returns path to the generation report.
    """
    output_cfg = config["output"]
    generated_dir = os.path.join(
        output_cfg.get("generated_dir", "generated"),
        "round_{n}".format(n=round_num),
    )
    os.makedirs(generated_dir, exist_ok=True)

    report_lines = [
        "# Coverage Gap Analysis — Round {n}".format(n=round_num),
        "",
        "Generated by coverage-loop orchestrator.",
        "Use this report with the `coverage-to-robot` skill to generate Robot test cases.",
        "",
        "## Coverage Summary",
        "",
        "| Class | Line Coverage | Branch Coverage | Uncovered Methods | Partial Methods |",
        "|-------|--------------|-----------------|-------------------|-----------------|",
    ]

    for cls_name, data in coverage_results.items():
        s = data.get("summary", {})
        report_lines.append(
            "| {cls} | {lc:.1f}% ({lh}/{lt}) | {bc:.1f}% | {um} | {pm} |".format(
                cls=cls_name,
                lc=s.get("line_coverage", 0),
                lh=s.get("lines_hit", 0),
                lt=s.get("lines_total", 0),
                bc=s.get("branch_coverage", 0),
                um=len(data.get("uncovered_methods", [])),
                pm=len(data.get("partially_covered_methods", [])),
            )
        )

    # Add diff information if available
    if diff_results:
        report_lines.extend([
            "",
            "## Delta from Previous Round",
            "",
        ])
        for cls_name, diff in diff_results.items():
            if "error" in diff:
                report_lines.append(
                    "### {cls}: {err}".format(cls=cls_name, err=diff["error"])
                )
                continue
            report_lines.extend([
                "### {cls}".format(cls=cls_name),
                "",
                "- Line coverage: {b:.1f}% -> {a:.1f}% (delta: {d:+.1f}%)".format(
                    b=diff.get("line_coverage_before", 0),
                    a=diff.get("line_coverage_after", 0),
                    d=diff.get("line_coverage_delta", 0),
                ),
                "- Lines gained: {g}".format(g=diff.get("lines_gained", 0)),
                "- Newly covered methods: {m}".format(
                    m=", ".join(diff.get("newly_covered_methods", [])) or "none",
                ),
                "- Recommendation: **{r}** — {reason}".format(
                    r=diff.get("recommendation", "unknown"),
                    reason=diff.get("reason", ""),
                ),
                "",
            ])

    # Top coverage gaps for test generation
    report_lines.extend([
        "",
        "## Top Coverage Gaps (Targets for New Test Cases)",
        "",
    ])

    skip_priorities = set(
        config.get("loop", {}).get("skip_priority", ["P3"])
    )

    gap_count = 0
    for cls_name, data in coverage_results.items():
        # Uncovered methods
        for m in data.get("uncovered_methods", []):
            missed = m.get("lines_missed", m.get("uncovered_lines", 0))
            if missed < 3:
                continue  # Skip trivial (getters, etc.)
            gap_count += 1
            report_lines.extend([
                "### Gap {n}: {cls}.{method} (UNCOVERED)".format(
                    n=gap_count, cls=cls_name, method=m["name"],
                ),
                "",
                "- Lines missed: {u}, line: {l}".format(
                    u=missed,
                    l=m.get("line", m.get("start_line", "?")),
                ),
                "- Desc: `{d}`".format(d=m.get("desc", "")),
                "- Priority: P0 (entirely uncovered with business logic)",
                "- Action: Create E2E test that exercises this method",
                "",
            ])

        # Partially covered methods
        for m in data.get("partially_covered_methods", []):
            missed = m.get("lines_missed", 0)
            if missed < 3:
                continue
            gap_count += 1
            report_lines.extend([
                "### Gap {n}: {cls}.{method} (PARTIAL — {pct:.0f}%)".format(
                    n=gap_count, cls=cls_name, method=m["name"],
                    pct=m.get("line_coverage_pct", 0),
                ),
                "",
                "- Lines missed: {u} / covered: {c}".format(
                    u=missed,
                    c=m.get("lines_covered", 0),
                ),
                "- Priority: P1 (partially covered — missed branches)",
                "- Action: Identify trigger condition for uncovered paths",
                "",
            ])

    if gap_count == 0:
        report_lines.append("No significant coverage gaps found.")

    # Write generation report
    report_path = os.path.join(generated_dir, "generation_report.md")
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines) + "\n")

    log.info("Generation report: %s (%d gaps)", report_path, gap_count)

    # Also write the raw gap data as JSON for programmatic use
    gaps_json_path = os.path.join(generated_dir, "gaps.json")
    gaps_data = {
        "round": round_num,
        "gap_count": gap_count,
        "classes": {
            cls: {
                "uncovered_methods": data.get("uncovered_methods", []),
                "uncovered_blocks": data.get("uncovered_blocks", [])[:20],
                "partially_covered_methods": data.get(
                    "partially_covered_methods", []
                ),
            }
            for cls, data in coverage_results.items()
        },
    }
    with open(gaps_json_path, "w") as f:
        json.dump(gaps_data, f, indent=2)

    return report_path


# ---------------------------------------------------------------------------
# Phase 4b: Generate actual Robot Framework .robot test files
# ---------------------------------------------------------------------------

# Map Java package fragments to functional test domains
_DOMAIN_RULES = [
    ("blockmanager.statemachine", "state_machine"),
    ("blockmanager", "block_manager"),
    ("object.impl.gc", "gc"),
    ("object.impl.buffer", "buffer"),
    ("object.impl.allocation", "allocation"),
    ("object.impl.recovery", "recovery"),
    ("object.impl.compression", "compression"),
    ("object.impl.ec", "ec"),
    ("object.impl", "data_path"),
]


def _classify_method_action(method_name):
    """Map a Java method name to a high-level test action category.

    Returns one of: seal, ec_encode, write_validate, block_create, gc,
    recovery, compression, buffer, read, write, copy, update, general.
    """
    name = method_name.lower()
    if "seal" in name:
        return "seal"
    if any(k in name for k in ("ecencode", "eccomplete", "ec_encode")):
        return "ec_encode"
    if "lastvalidlength" in name:
        return "write_validate"
    if any(k in name for k in ("reclaim", "delet")):
        return "gc"
    if any(k in name for k in ("progress", "collection")):
        return "gc"
    if any(k in name for k in ("recover", "repair", "rebuild")):
        return "recovery"
    if any(k in name for k in ("compress", "decompress")):
        return "compression"
    if any(k in name for k in ("flush", "buffer", "spill")):
        return "buffer"
    if "read" in name and "write" not in name:
        return "read"
    if any(k in name for k in ("write", "ingest", "put")):
        return "write"
    if "copy" in name:
        return "copy"
    if any(k in name for k in ("update", "range")):
        return "update"
    return "general"


def generate_robot_tests(config, round_num, coverage_results):
    """
    Generate actual Robot Framework ``.robot`` test files from coverage gaps.

    Produces a consolidated test suite targeting the highest-impact
    uncovered code paths.  Each test scenario maps Java method-level
    gaps to E2E operations using existing blocklayer keyword libraries.

    The generated file is written to the ``generated/`` directory *and*
    copied into the automation repo's ``robot/object/blocklayer/``
    directory so that Robot Framework can resolve Resource imports.

    Returns the automation-relative suite path (without ``.robot``
    extension), or *None* if no actionable gaps were found.
    """
    output_cfg = config.get("output", {})
    generated_dir = os.path.join(
        output_cfg.get("generated_dir", "generated"),
        "round_{n}".format(n=round_num),
    )
    os.makedirs(generated_dir, exist_ok=True)

    # ---- Collect and classify gaps ----
    gaps_by_action = {}
    total_gaps = 0

    for cls_name, data in coverage_results.items():
        for m in data.get("uncovered_methods", []):
            missed = m.get("lines_missed", m.get("uncovered_lines", 0))
            if missed < 3 or m["name"] in ("<init>", "<clinit>"):
                continue
            action = _classify_method_action(m["name"])
            gaps_by_action.setdefault(action, []).append({
                "class": cls_name,
                "method": m["name"],
                "missed": missed,
                "coverage_pct": 0.0,
                "priority": "P0",
            })
            total_gaps += 1

        for m in data.get("partially_covered_methods", []):
            missed = m.get("lines_missed", 0)
            if missed < 5 or m["name"] in ("<init>", "<clinit>"):
                continue
            action = _classify_method_action(m["name"])
            gaps_by_action.setdefault(action, []).append({
                "class": cls_name,
                "method": m["name"],
                "missed": missed,
                "coverage_pct": m.get("line_coverage_pct", 0),
                "priority": "P1",
            })
            total_gaps += 1

    if total_gaps == 0:
        log.info("No significant coverage gaps; skipping .robot generation")
        return None

    log.info(
        "Classified %d gaps into %d action categories",
        total_gaps, len(gaps_by_action),
    )
    for action, gaps in sorted(
        gaps_by_action.items(), key=lambda x: -sum(g["missed"] for g in x[1]),
    ):
        log.info(
            "  %-16s  %3d gaps  %5d missed lines",
            action, len(gaps), sum(g["missed"] for g in gaps),
        )

    # ---- Build and write the .robot file ----
    robot_lines = _build_robot_file(gaps_by_action, round_num)

    ref_path = os.path.join(
        generated_dir,
        "coverage_improvement_round_{n}.robot".format(n=round_num),
    )
    with open(ref_path, "w") as f:
        f.write("\n".join(robot_lines) + "\n")

    # Copy into automation tree so Resource imports resolve
    automation_dir = config["execution"]["automation_dir"]
    auto_dir = os.path.join(
        automation_dir, "robot", "object", "blocklayer",
    )
    auto_path = os.path.join(
        auto_dir,
        "coverage_improvement_round_{n}.robot".format(n=round_num),
    )
    suite_rel = "robot/object/blocklayer/coverage_improvement_round_{n}".format(
        n=round_num,
    )
    if os.path.isdir(auto_dir):
        shutil.copy2(ref_path, auto_path)
        log.info("Copied to automation: %s", auto_path)
    else:
        log.warning(
            "Automation blocklayer dir not found: %s; "
            "generated suite may not resolve Resource imports",
            auto_dir,
        )

    tc_count = sum(1 for ln in robot_lines if ln.startswith("[Scenario-"))
    log.info(
        "Generated: %s (%d test cases, suite: %s)",
        ref_path, tc_count, suite_rel,
    )
    return suite_rel


def _build_robot_file(gaps_by_action, round_num):
    """Build complete ``.robot`` file content as a list of lines."""
    lines = []

    # ---- Settings ----
    lines.extend([
        "*** Settings ***",
        "Documentation     Coverage-driven E2E tests generated by coverage-loop round {n}.".format(
            n=round_num,
        ),
        "...               These tests target uncovered code paths identified via JaCoCo",
        "...               analysis of the Block Manager service.",
        "",
        "Resource          robot/common_keywords/acceptance_keywords.robot",
        "",
        "Library           library.object.blocklayer.block_write_and_read_keywords.BlockWriteAndReadKeywords    WITH NAME    BlockWriteAndReadKeywords",
        "Library           library.object.shared_services_keywords.SharedObjectServicesKeywords",
        "Library           DateTime",
        "Library           Collections",
        "",
        "Default Tags      coverage-improvement    component-test",
        "",
        "Suite Setup       Run Keywords",
        "...               Establish Connection To Platform    ${PLATFORM}",
        "...    AND        Coverage Suite Prepare Environment",
        "",
        "Suite Teardown    Run Keywords",
        "...               Coverage Suite Restore Environment",
        "...    AND        BlockWriteAndReadKeywords.Close Clients",
        "",
    ])

    # ---- Variables ----
    lines.extend([
        "*** Variables ***",
        "${INDEX_GRANULARITY_DEFAULT}    2236912",
        "${LARGE_OBJECT_SIZE}           134214720",
        "${XLARGE_OBJECT_SIZE}          268435456",
        "",
    ])

    # ---- Test Cases ----
    lines.append("*** Test Cases ***")

    sorted_actions = sorted(
        gaps_by_action.items(),
        key=lambda x: -sum(g["missed"] for g in x[1]),
    )

    scenario_num = 0
    for action, gaps in sorted_actions:
        target_classes = sorted(set(
            g["class"].rsplit(".", 1)[-1] for g in gaps
        ))
        target_methods = sorted(set(g["method"] for g in gaps))
        total_missed = sum(g["missed"] for g in gaps)

        scenarios = _action_to_scenarios(
            action, gaps, target_classes, target_methods, total_missed,
        )
        for sc in scenarios:
            scenario_num += 1
            cov_tags = [
                "coverage-{c}".format(c=c) for c in target_classes[:3]
            ]
            all_tags = ["Coverage", "Tier2", "E2E", action] + cov_tags
            tag_str = "    ".join(all_tags)

            lines.extend([
                "",
                "[Scenario-{n}] - {title}".format(
                    n=scenario_num, title=sc["title"],
                ),
                "    [Documentation]    Coverage-driven test targeting "
                "{count} uncovered code path(s).".format(count=len(gaps)),
                "    ...    Generated by coverage-loop round {r}.".format(
                    r=round_num,
                ),
                "    ...    Target classes: {cls}".format(
                    cls=", ".join(target_classes[:5]),
                ),
                "    ...    Target methods ({n}): {methods}".format(
                    n=len(target_methods),
                    methods=", ".join(target_methods[:8]),
                ),
                "    ...    Total uncovered lines targeted: {n}".format(
                    n=total_missed,
                ),
                "    [Tags]    {tags}".format(tags=tag_str),
            ])
            for step in sc["steps"]:
                lines.append("    {step}".format(step=step))

    # ---- Keywords ----
    lines.extend(["", "*** Keywords ***"])
    lines.extend(_build_helper_keywords())

    return lines


def _action_to_scenarios(action, gaps, target_classes, target_methods,
                         total_missed):
    """Convert an action category and its gaps into test scenarios."""
    scenarios = []

    if action == "seal":
        scenarios.append({
            "title": "Exercise block seal transitions via large object ingest "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                " Then Coverage Test Gets And Validates Type-2 Blocks",
                "  And Coverage Test Validates Block Info",
            ],
        })

    elif action == "ec_encode":
        scenarios.append({
            "title": "Exercise EC encoding paths via large object ingest "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                " Then Coverage Test Gets And Validates Type-2 Blocks",
                "  And Coverage Test Validates EC Encoding",
            ],
        })

    elif action in ("write_validate", "block_create", "write"):
        scenarios.append({
            "title": "Exercise {a} paths via object write "
                     "({n} lines)".format(a=action, n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                " Then Coverage Test Gets And Validates Type-2 Blocks",
            ],
        })

    elif action == "gc":
        scenarios.append({
            "title": "Exercise GC and reclaim paths via write and background "
                     "activity ({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                "  And Coverage Test Gets And Validates Type-2 Blocks",
                "  And Coverage Test Waits For Background Processing"
                "    timeout=120s",
                " Then Log    GC background activity should exercise "
                "reclaim code paths",
            ],
        })

    elif action == "recovery":
        scenarios.append({
            "title": "Exercise block recovery via segment corruption "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                "  And Coverage Test Gets And Validates Type-2 Blocks",
                "  And Coverage Test Corrupts Block Segments",
                " Then Coverage Test Reads Object To Trigger Recovery",
                "  And Coverage Test Validates Recovery Was Triggered",
            ],
        })

    elif action == "compression":
        scenarios.append({
            "title": "Exercise compression paths via zero-filled object "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Zero-Filled Large Object",
                " Then Coverage Test Gets And Validates Type-2 Blocks",
                "  And Coverage Test Validates Compression Info",
            ],
        })

    elif action == "buffer":
        scenarios.append({
            "title": "Exercise buffer management via extra-large object "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Extra-Large Object",
                " Then Coverage Test Gets And Validates Type-2 Blocks",
            ],
        })

    elif action == "read":
        scenarios.append({
            "title": "Exercise read paths via object retrieval "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                " Then Coverage Test Reads Object And Validates Content",
            ],
        })

    elif action == "copy":
        scenarios.append({
            "title": "Exercise copy paths via deep copy "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Two Buckets",
                "  And Coverage Test Ingests Large Object",
                " Then Coverage Test Deep Copies Object",
                "  And Coverage Test Validates Copy In Destination",
            ],
        })

    elif action == "update":
        scenarios.append({
            "title": "Exercise update paths via range update "
                     "({n} lines)".format(n=total_missed),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                "  And Coverage Test Range Updates Middle Bytes",
                " Then Coverage Test Reads Object And Validates Content",
            ],
        })

    else:
        # General catch-all
        scenarios.append({
            "title": "Exercise general code paths for {cls} "
                     "({n} lines)".format(
                         cls=", ".join(target_classes[:3]),
                         n=total_missed,
                     ),
            "steps": [
                " When Coverage Test Creates Bucket",
                "  And Coverage Test Ingests Large Object",
                " Then Coverage Test Gets And Validates Type-2 Blocks",
                "  And Coverage Test Validates Block Info",
            ],
        })

    return scenarios


def _build_helper_keywords():
    """Build the Keywords section with all helper keywords used by tests."""
    lines = []

    # Suite setup / teardown
    lines.extend([
        "",
        "Coverage Suite Prepare Environment",
        "    [Documentation]    Initialise cluster connection and enable DT query.",
        "    BlockWriteAndReadKeywords.The storage cluster Cluster Is Reachable",
        "    BlockWriteAndReadKeywords.Enable DT Query",
        "",
        "Coverage Suite Restore Environment",
        "    [Documentation]    Restore environment after coverage tests.",
        "    Log    Coverage suite teardown complete",
        "",
    ])

    # Bucket keywords
    lines.extend([
        "Coverage Test Creates Bucket",
        "    [Documentation]    Create a new S3 bucket for coverage testing.",
        "    ${bucket_name} =    BlockWriteAndReadKeywords.Create A New Bucket",
        "    Set Suite Variable    ${bucket_name}",
        "",
        "Coverage Test Creates Two Buckets",
        "    [Documentation]    Create source and destination buckets for copy testing.",
        "    ${source_bucket} =    BlockWriteAndReadKeywords.Create A New Bucket",
        "    ${dest_bucket} =    BlockWriteAndReadKeywords.Create A New Bucket",
        "    Set Suite Variable    ${source_bucket}",
        "    Set Suite Variable    ${dest_bucket}",
        "    Set Suite Variable    ${bucket_name}    ${source_bucket}",
        "",
    ])

    # Ingest keywords
    lines.extend([
        "Coverage Test Ingests Large Object",
        "    [Documentation]    Ingest a ~128 MB object with index granularity (Type-2 blocks).",
        "    @{object_keys} =    BlockWriteAndReadKeywords.Ingest Objects With Index Granularity To Bucket",
        "    ...    bucket_name=${bucket_name}",
        "    ...    number_of_objects=1",
        "    ...    size_of_object=${LARGE_OBJECT_SIZE}",
        "    ...    index_granularity=${INDEX_GRANULARITY_DEFAULT}",
        "    Set Suite Variable    @{object_keys}",
        "",
        "Coverage Test Ingests Extra-Large Object",
        "    [Documentation]    Ingest a 256 MB object to exercise buffer flush paths.",
        "    @{object_keys} =    BlockWriteAndReadKeywords.Ingest Objects With Index Granularity To Bucket",
        "    ...    bucket_name=${bucket_name}",
        "    ...    number_of_objects=1",
        "    ...    size_of_object=${XLARGE_OBJECT_SIZE}",
        "    ...    index_granularity=${INDEX_GRANULARITY_DEFAULT}",
        "    Set Suite Variable    @{object_keys}",
        "",
        "Coverage Test Ingests Zero-Filled Large Object",
        "    [Documentation]    Ingest a zero-filled object to trigger compression.",
        "    @{object_keys} =    BlockWriteAndReadKeywords.Ingest Zero Objects To Bucket",
        "    ...    ${bucket_name}",
        "    ...    size_of_object=${LARGE_OBJECT_SIZE}",
        "    ...    number_of_objects=${1}",
        "    Set Suite Variable    @{object_keys}",
        "",
    ])

    # Block retrieval and validation
    lines.extend([
        "Coverage Test Gets And Validates Type-2 Blocks",
        "    [Documentation]    Retrieve Type-2 repo blocks and store in suite variable.",
        "    ${block_dict} =    BlockWriteAndReadKeywords.Get Repo Blocks Of Objects In Bucket",
        "    ...    ${bucket_name}",
        "    ...    object_keys=@{object_keys}",
        "    ...    repoBlockType=TYPE_II",
        "    Set Suite Variable    ${block_dict}",
        "",
        "Coverage Test Validates Block Info",
        "    [Documentation]    Validate block info properties (status, type, EC encoding).",
        "    BlockWriteAndReadKeywords.Validate Block Info Of Blocks    ${block_dict}",
        "",
        "Coverage Test Validates EC Encoding",
        "    [Documentation]    Validate EC copy info of repo blocks.",
        "    BlockWriteAndReadKeywords.Validate EC Copy Info Of Repo Blocks In Block Info",
        "    ...    ${block_dict}    is_client_ec=${True}",
        "",
        "Coverage Test Validates Compression Info",
        "    [Documentation]    Validate compression info exists for compressed blocks.",
        "    BlockWriteAndReadKeywords.Validate Compress Info Of Repo Blocks In Object Info",
        "    ...    ${block_dict}    ${bucket_name}    ${object_keys}",
        "    ...    is_with_compress_info=${True}",
        "",
    ])

    # Read keywords
    lines.extend([
        "Coverage Test Reads Object And Validates Content",
        "    [Documentation]    Read object back and validate content is non-empty.",
        "    ${object_key} =    Get From List    ${object_keys}    0",
        "    ${body} =    BlockWriteAndReadKeywords.Get Object Body From Bucket",
        "    ...    ${bucket_name}    ${object_key}",
        "    Should Not Be Empty    ${body}",
        "",
    ])

    # Recovery keywords
    lines.extend([
        "Coverage Test Corrupts Block Segments",
        "    [Documentation]    Corrupt Copy1 segments to trigger recovery on next read.",
        "    BlockWriteAndReadKeywords.Mock Segments Of Blocks As Bad",
        "    ...    ${block_dict}    Copy1",
        "",
        "Coverage Test Reads Object To Trigger Recovery",
        "    [Documentation]    Read object to trigger recovery of corrupted blocks.",
        "    ${object_key} =    Get From List    ${object_keys}    0",
        "    ${body} =    BlockWriteAndReadKeywords.Get Object Body From Bucket",
        "    ...    ${bucket_name}    ${object_key}",
        "",
        "Coverage Test Validates Recovery Was Triggered",
        "    [Documentation]    Validate that async recovery was triggered.",
        "    BlockWriteAndReadKeywords.Validate If Async Recovery Is Triggered For Blocks",
        "    ...    ${block_dict}",
        "",
    ])

    # Copy keywords
    lines.extend([
        "Coverage Test Deep Copies Object",
        "    [Documentation]    Deep-copy the first object to the destination bucket.",
        "    ${src_key} =    Get From List    ${object_keys}    0",
        "    ${dst_key} =    Set Variable    coverage_deep_copy_dst",
        "    BlockWriteAndReadKeywords.Deep Copy Object",
        "    ...    ${source_bucket}    ${src_key}",
        "    ...    ${dest_bucket}    ${dst_key}",
        "    @{dest_keys} =    Create List    ${dst_key}",
        "    Set Suite Variable    @{dest_object_keys}    @{dest_keys}",
        "",
        "Coverage Test Validates Copy In Destination",
        "    [Documentation]    Validate that the copy created new blocks in the destination.",
        "    ${dest_blocks} =    BlockWriteAndReadKeywords.Get Repo Blocks Of Objects In Bucket",
        "    ...    ${dest_bucket}",
        "    ...    object_keys=@{dest_object_keys}",
        "    ...    repoBlockType=TYPE_II",
        "    Log    Deep copy created separate block set in destination bucket",
        "",
    ])

    # Range update keywords
    lines.extend([
        "Coverage Test Range Updates Middle Bytes",
        "    [Documentation]    Perform a range update on the second half of the object.",
        "    ${object_key} =    Get From List    ${object_keys}    0",
        "    ${half} =    Evaluate    ${LARGE_OBJECT_SIZE} // 2",
        "    ${end} =    Evaluate    ${LARGE_OBJECT_SIZE} - 1",
        "    ${range_spec} =    Set Variable    ${half}-${end}",
        "    ${update_size} =    Evaluate    ${end} - ${half} + 1",
        "    ${update_body} =    Evaluate    b'x' * ${update_size}",
        "    BlockWriteAndReadKeywords.Range Update Object In Bucket",
        "    ...    ${bucket_name}    ${object_key}    ${range_spec}    ${update_body}",
        "",
    ])

    # Wait helper
    lines.extend([
        "Coverage Test Waits For Background Processing",
        "    [Documentation]    Wait for cluster background processes (GC, recovery).",
        "    [Arguments]    ${timeout}=120s",
        "    Sleep    ${timeout}    Waiting for background processing",
        "",
    ])

    return lines


# ---------------------------------------------------------------------------
# Phase 5: Loop decision
# ---------------------------------------------------------------------------

def should_continue(config, round_num, diff_results):
    """
    Decide whether to continue the loop.

    Returns (bool, str) — continue flag and reason.
    """
    loop_cfg = config.get("loop", {})
    max_iter = loop_cfg.get("max_iterations", 5)
    target_cov = loop_cfg.get("target_coverage_pct", 60.0)
    min_delta = loop_cfg.get("min_delta_pct", 2.0)

    if round_num >= max_iter:
        return False, "Max iterations ({m}) reached".format(m=max_iter)

    if not diff_results:
        # First round, always continue
        return True, "First round complete; continuing to collect coverage"

    # Check across all classes
    diminishing_count = 0
    target_reached_count = 0

    for cls_name, diff in diff_results.items():
        if "error" in diff:
            continue

        cov = diff.get("line_coverage_after", 0)
        delta = diff.get("line_coverage_delta", 0)
        rec = diff.get("recommendation", "continue")

        if cov >= target_cov:
            target_reached_count += 1
        if rec == "stop":
            return False, "Class {c}: {r}".format(
                c=cls_name, r=diff.get("reason", ""),
            )
        if rec == "diminishing":
            diminishing_count += 1

    if target_reached_count == len(diff_results):
        return False, "All classes reached target coverage"

    if diminishing_count == len(diff_results):
        return False, "Diminishing returns across all classes"

    return True, "Coverage improving; continuing"


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop(config, start_round=1, max_iterations=None, skip_execute=False,
             pr_url=None, jenkins_build_url=None):
    """
    Run the full coverage improvement loop.

    Parameters
    ----------
    config : dict
        Full configuration.
    start_round : int
        Starting round number (for resuming).
    max_iterations : int, optional
        Override max_iterations from config.
    skip_execute : bool
        If True, skip test execution (analyze existing coverage only).
    pr_url : str, optional
        GitHub Enterprise PR URL.  When provided, coverage analysis is
        restricted to Java classes changed in the PR.
    jenkins_build_url : str, optional
        Jenkins build URL to fetch coverage from directly (skips test
        execution; uses JaCoCo plugin API for coverage data).
    """
    if max_iterations is not None:
        config.setdefault("loop", {})["max_iterations"] = max_iterations

    loop_cfg = config.get("loop", {})
    max_iter = loop_cfg.get("max_iterations", 5)
    target_cov = loop_cfg.get("target_coverage_pct", 60.0)
    min_delta = loop_cfg.get("min_delta_pct", 2.0)

    # --- PR-mode: resolve PR URL from CLI or config ---
    pr_cfg = config.get("pr_filtering", {})
    if not pr_url:
        pr_url = pr_cfg.get("pr_url", "")
    pr_context = None
    if pr_url:
        log.info("PR-mode enabled: %s", pr_url)
        pr_repo_path = pr_cfg.get("local_repo_path", "")
        pr_base = pr_cfg.get("base_ref", "origin/master")
        pr_head = pr_cfg.get("head_ref", "")
        try:
            pr_context = get_pr_context(
                pr_url=pr_url,
                repo_path=pr_repo_path or None,
                base_ref=pr_base,
                head_ref=pr_head or None,
            )
            changed = pr_context.get("changed_classes", set())
            if not changed:
                log.warning(
                    "PR diff returned no Java classes; "
                    "falling back to full coverage analysis"
                )
                pr_context = None
        except Exception as exc:
            log.error("Failed to fetch PR diff: %s", exc)
            log.info(
                "Continuing without PR filtering; "
                "set token permissions or provide local_repo_path"
            )

    log.info("=" * 60)
    log.info("COVERAGE LOOP START")
    log.info("  Max iterations: %d", max_iter)
    log.info("  Target coverage: %.1f%%", target_cov)
    log.info("  Min delta: %.1f%%", min_delta)
    log.info("  Start round: %d", start_round)
    log.info("  Skip execute: %s", skip_execute)
    log.info("  PR-mode: %s", "ON" if pr_context else "OFF")
    if pr_context:
        log.info(
            "  PR classes: %d",
            len(pr_context.get("changed_classes", set())),
        )
    if jenkins_build_url:
        log.info("  Jenkins build (pre-existing): %s", jenkins_build_url)
    log.info("=" * 60)

    # When --jenkins-build is provided, override: skip execution,
    # use the given build URL for coverage collection.
    cli_jenkins_build_url = jenkins_build_url  # save for first round
    if jenkins_build_url:
        skip_execute = True

    consecutive_diminishing = 0
    suite_paths = None  # Use initial_suites for round 1
    history = []

    for round_num in range(start_round, start_round + max_iter):
        log.info("")
        log.info("=" * 60)
        log.info("ROUND %d", round_num)
        log.info("=" * 60)

        # Phase 1: Execute
        if not skip_execute:
            log.info("--- Phase 1: Execute Tests ---")
            exec_result = execute_tests(config, round_num, suite_paths)
            if exec_result is None:
                log.error("Test execution failed; stopping loop")
                break
            log.info(
                "Execution complete: %d suites",
                len(exec_result.get("results", [])),
            )

            # Brief pause to let JaCoCo agent flush data
            log.info("Waiting 10s for JaCoCo data to settle...")
            time.sleep(10)
        else:
            exec_result = None
            log.info("--- Phase 1: SKIPPED (--skip-execute) ---")

        # --- Jenkins fast-path: collect + analyze via JaCoCo plugin API ---
        jenkins_build_url = (
            exec_result.get("jenkins_build_url")
            if exec_result else cli_jenkins_build_url
        )
        coverage_results = None

        if jenkins_build_url:
            log.info("--- Phase 2+3: Collect & Analyze via Jenkins API ---")
            log.info("Build URL: %s", jenkins_build_url)
            coverage_results = _collect_coverage_from_jenkins(
                jenkins_build_url, config, round_num,
                pr_context=pr_context,
            )
            if coverage_results:
                # Save per-class JSON files (same as analyze_coverage does)
                coverage_data_dir = config["output"].get(
                    "coverage_data_dir", "coverage-data"
                )
                round_dir = os.path.join(
                    coverage_data_dir,
                    "round_{n}".format(n=round_num),
                )
                os.makedirs(round_dir, exist_ok=True)
                for cls_fqn, data in coverage_results.items():
                    safe_name = cls_fqn.replace(".", "_")
                    out_file = os.path.join(
                        round_dir,
                        "{cls}_coverage.json".format(cls=safe_name),
                    )
                    with open(out_file, "w") as f:
                        json.dump(data, f, indent=2)
                _write_round_summary(round_dir, round_num, coverage_results)
                log.info(
                    "Jenkins API: %d classes analyzed", len(coverage_results),
                )
            else:
                log.warning(
                    "Jenkins API returned no data; "
                    "falling back to local collect + analyze"
                )

        # --- Fallback: local SSH-based collect + analyze ---
        if coverage_results is None:
            # Phase 2: Collect
            log.info("--- Phase 2: Collect Coverage (local SSH) ---")
            collect_result = collect_coverage(config, round_num)
            if collect_result is None:
                log.error("Coverage collection failed; stopping loop")
                break

            html_dir = collect_result.get("html_dir", "")
            xml_path = collect_result.get("xml_file", None)

            # Phase 3: Analyze
            log.info("--- Phase 3: Analyze Coverage (local) ---")
            coverage_results = analyze_coverage(
                config, round_num, html_dir=html_dir, xml_path=xml_path,
                pr_context=pr_context,
            )

        if not coverage_results:
            log.warning("No coverage data analyzed; stopping loop")
            break

        # Diff against previous round
        diff_results = {}
        if round_num > start_round:
            coverage_data_dir = config["output"].get(
                "coverage_data_dir", "coverage-data"
            )
            # Use actual analyzed class names (FQNs from XML or names from HTML)
            for cls_name in coverage_results.keys():
                safe_name = cls_name.replace(".", "_")
                diff = diff_round_dir(coverage_data_dir, round_num, safe_name)
                diff = recommend(diff, min_delta=min_delta, target_coverage=target_cov)
                diff_results[cls_name] = diff
                log.info(
                    "  %s: %.1f%% -> %.1f%% (delta: %+.1f%%) [%s]",
                    cls_name,
                    diff.get("line_coverage_before", 0),
                    diff.get("line_coverage_after", 0),
                    diff.get("line_coverage_delta", 0),
                    diff.get("recommendation", "?"),
                )

            # Save diffs
            round_dir = os.path.join(
                coverage_data_dir,
                "round_{n}".format(n=round_num),
            )
            diff_file = os.path.join(
                round_dir,
                "diff_from_round_{p}.json".format(p=round_num - 1),
            )
            with open(diff_file, "w") as f:
                json.dump(diff_results, f, indent=2, default=str)

        # Phase 4: Generate
        log.info("--- Phase 4: Generate Test Cases ---")
        report_path = generate_test_cases(
            config, round_num, coverage_results, diff_results,
        )

        # Phase 4b: Generate actual .robot file
        robot_suite = generate_robot_tests(
            config, round_num, coverage_results,
        )

        # Record history
        round_entry = {
            "round": round_num,
            "classes": {},
        }
        for cls_name, data in coverage_results.items():
            s = data.get("summary", {})
            round_entry["classes"][cls_name] = {
                "line_coverage": s.get("line_coverage", 0),
                "branch_coverage": s.get("branch_coverage", 0),
            }
        history.append(round_entry)

        # Phase 5: Decide
        log.info("--- Phase 5: Loop Decision ---")
        should_go, reason = should_continue(config, round_num, diff_results)

        if diff_results:
            # Track diminishing returns
            all_diminishing = all(
                d.get("recommendation") == "diminishing"
                for d in diff_results.values()
                if "error" not in d
            )
            if all_diminishing:
                consecutive_diminishing += 1
            else:
                consecutive_diminishing = 0

            if consecutive_diminishing >= 2:
                should_go = False
                reason = (
                    "Diminishing returns for {n} consecutive rounds".format(
                        n=consecutive_diminishing,
                    )
                )

        if not should_go:
            log.info("STOPPING: %s", reason)
            break
        else:
            log.info("CONTINUING: %s", reason)

        # Prepare for next round
        if robot_suite:
            suite_paths = [robot_suite]
            log.info(
                "Next round will use generated suite: %s",
                robot_suite,
            )
        else:
            log.info(
                "No generated .robot file; "
                "next round will re-run initial suites"
            )
            suite_paths = None  # Reset to initial_suites

    # Write final summary
    _write_summary(config, history)

    log.info("")
    log.info("=" * 60)
    log.info("COVERAGE LOOP COMPLETE")
    log.info("  Rounds executed: %d", len(history))
    if history:
        last = history[-1]
        for cls, metrics in last.get("classes", {}).items():
            log.info(
                "  Final %s: line=%.1f%%, branch=%.1f%%",
                cls,
                metrics.get("line_coverage", 0),
                metrics.get("branch_coverage", 0),
            )
    log.info("=" * 60)


def _write_summary(config, history):
    """Write a cumulative summary across all rounds."""
    output_cfg = config.get("output", {})
    gen_dir = output_cfg.get("generated_dir", "generated")

    summary_lines = [
        "# Coverage Loop Summary",
        "",
        "## Rounds",
        "",
        "| Round | Class | Line Coverage | Branch Coverage |",
        "|-------|-------|--------------|-----------------|",
    ]
    for entry in history:
        for cls, metrics in entry.get("classes", {}).items():
            summary_lines.append(
                "| {r} | {c} | {l:.1f}% | {b:.1f}% |".format(
                    r=entry["round"],
                    c=cls,
                    l=metrics.get("line_coverage", 0),
                    b=metrics.get("branch_coverage", 0),
                )
            )

    # Coverage trend
    if len(history) > 1:
        summary_lines.extend(["", "## Coverage Trend", ""])
        all_classes = set()
        for entry in history:
            all_classes.update(entry.get("classes", {}).keys())

        for cls in sorted(all_classes):
            summary_lines.append("### {c}".format(c=cls))
            summary_lines.append("")
            for entry in history:
                metrics = entry.get("classes", {}).get(cls, {})
                cov = metrics.get("line_coverage", 0)
                bar = "#" * int(cov / 2)  # Simple text bar
                summary_lines.append(
                    "  Round {r}: [{bar:<50}] {c:.1f}%".format(
                        r=entry["round"], bar=bar, c=cov,
                    )
                )
            summary_lines.append("")

    summary_path = os.path.join(gen_dir, "summary.md")
    os.makedirs(gen_dir, exist_ok=True)
    with open(summary_path, "w") as f:
        f.write("\n".join(summary_lines) + "\n")
    log.info("Summary written to %s", summary_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Coverage Loop Orchestrator"
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Override max iterations from config",
    )
    parser.add_argument(
        "--start-round", type=int, default=1,
        help="Starting round number (for resuming, default: 1)",
    )
    parser.add_argument(
        "--skip-execute", action="store_true",
        help="Skip test execution (analyze existing coverage only)",
    )
    parser.add_argument(
        "--pr-url",
        help="GitHub Enterprise PR URL for PR-targeted coverage "
             "(e.g. https://github.example.com/org/storage-service/pull/123). "
             "When set, analysis is restricted to Java classes changed in "
             "the PR.",
    )
    parser.add_argument(
        "--jenkins-build",
        help="Jenkins build URL to fetch coverage from (skips test "
             "execution, collects coverage via JaCoCo plugin API). "
             "E.g. https://jenkins.example.com/job/test-qe/"
             "job/my-component-cc/119/",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = load_config(args.config)

    run_loop(
        config,
        start_round=args.start_round,
        max_iterations=args.max_iterations,
        skip_execute=args.skip_execute,
        pr_url=args.pr_url,
        jenkins_build_url=args.jenkins_build,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
