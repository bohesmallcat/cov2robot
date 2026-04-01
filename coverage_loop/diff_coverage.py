#!/usr/bin/env python3
"""
Compare two rounds of coverage data and compute delta metrics.

Usage:
    python diff_coverage.py <round_N-1_json> <round_N_json>
    python diff_coverage.py --dir coverage-data --round 2

Output: JSON to stdout with delta metrics and recommendation.
"""

import argparse
import json
import os
import sys


def load_json(path):
    """Load a coverage JSON file produced by parse_coverage.py."""
    with open(path, "r") as f:
        return json.load(f)


def diff_coverage(before, after):
    """
    Compare two parse_coverage.py outputs and produce delta metrics.

    Parameters
    ----------
    before : dict
        Coverage JSON from the earlier round.
    after : dict
        Coverage JSON from the later round.

    Returns
    -------
    dict
        Delta metrics including coverage change, newly covered methods,
        remaining gaps, and a recommendation.
    """
    before_summary = before.get("summary", {})
    after_summary = after.get("summary", {})

    line_before = before_summary.get("line_coverage", 0.0)
    line_after = after_summary.get("line_coverage", 0.0)
    line_delta = round(line_after - line_before, 2)

    branch_before = before_summary.get("branch_coverage", 0.0)
    branch_after = after_summary.get("branch_coverage", 0.0)
    branch_delta = round(branch_after - branch_before, 2)

    lines_hit_before = before_summary.get("lines_hit", 0)
    lines_hit_after = after_summary.get("lines_hit", 0)
    lines_gained = lines_hit_after - lines_hit_before

    # Method-level changes
    before_uncovered = {
        m["name"] for m in before.get("uncovered_methods", [])
    }
    after_uncovered = {
        m["name"] for m in after.get("uncovered_methods", [])
    }

    before_partial = {
        m["name"] for m in before.get("partially_covered_methods", [])
    }
    after_partial = {
        m["name"] for m in after.get("partially_covered_methods", [])
    }

    before_covered = {
        m["name"] for m in before.get("covered_methods", [])
    }
    after_covered = {
        m["name"] for m in after.get("covered_methods", [])
    }

    # Methods that moved from uncovered -> partially covered or covered
    newly_hit = (before_uncovered - after_uncovered)
    # Methods that moved from partial -> fully covered
    newly_full = (before_partial - after_partial) & after_covered

    # Remaining gaps (from after round)
    remaining_gaps = []
    for block in after.get("uncovered_blocks", []):
        remaining_gaps.append({
            "method": block.get("containing_method", "unknown"),
            "start_line": block.get("start_line"),
            "end_line": block.get("end_line"),
            "uncovered_lines": block.get("uncovered_line_count", 0),
        })

    # Sort by uncovered lines descending
    remaining_gaps.sort(key=lambda g: g["uncovered_lines"], reverse=True)

    # Partial method improvements
    partial_improvements = []
    before_partial_map = {
        m["name"]: m for m in before.get("partially_covered_methods", [])
    }
    for m in after.get("partially_covered_methods", []):
        name = m["name"]
        if name in before_partial_map:
            old_cov = before_partial_map[name].get("coverage_pct", 0.0)
            new_cov = m.get("coverage_pct", 0.0)
            if new_cov > old_cov:
                partial_improvements.append({
                    "method": name,
                    "coverage_before": round(old_cov, 1),
                    "coverage_after": round(new_cov, 1),
                    "delta": round(new_cov - old_cov, 1),
                })

    return {
        "file": after.get("file", before.get("file", "unknown")),
        "line_coverage_before": line_before,
        "line_coverage_after": line_after,
        "line_coverage_delta": line_delta,
        "branch_coverage_before": branch_before,
        "branch_coverage_after": branch_after,
        "branch_coverage_delta": branch_delta,
        "lines_gained": lines_gained,
        "newly_covered_methods": sorted(newly_hit),
        "newly_fully_covered_methods": sorted(newly_full),
        "partial_improvements": partial_improvements,
        "remaining_uncovered_methods": sorted(after_uncovered),
        "remaining_top_gaps": remaining_gaps[:20],
        "total_remaining_uncovered_blocks": len(
            after.get("uncovered_blocks", [])
        ),
    }


def recommend(diff_result, min_delta=2.0, target_coverage=60.0):
    """
    Add a recommendation to the diff result.

    Returns "stop" or "continue" with a reason.
    """
    cov = diff_result["line_coverage_after"]
    delta = diff_result["line_coverage_delta"]

    if cov >= target_coverage:
        diff_result["recommendation"] = "stop"
        diff_result["reason"] = (
            "Target coverage {t}% reached (current: {c}%)".format(
                t=target_coverage, c=cov,
            )
        )
    elif abs(delta) < 0.01:
        # No change at all
        diff_result["recommendation"] = "stop"
        diff_result["reason"] = "No coverage change detected"
    elif delta < min_delta:
        diff_result["recommendation"] = "diminishing"
        diff_result["reason"] = (
            "Delta {d}% is below threshold {t}%".format(
                d=delta, t=min_delta,
            )
        )
    elif not diff_result["remaining_top_gaps"]:
        diff_result["recommendation"] = "stop"
        diff_result["reason"] = "No remaining uncovered blocks"
    else:
        diff_result["recommendation"] = "continue"
        diff_result["reason"] = (
            "Coverage improved by {d}% ({g} lines gained); "
            "{n} uncovered blocks remain".format(
                d=delta,
                g=diff_result["lines_gained"],
                n=diff_result["total_remaining_uncovered_blocks"],
            )
        )

    return diff_result


def diff_round_dir(coverage_data_dir, round_num, target_class):
    """
    Diff coverage for a specific class between round_num-1 and round_num.

    Looks for files like: coverage-data/round_N/<class>_coverage.json
    """
    prev_round = round_num - 1
    prev_file = os.path.join(
        coverage_data_dir,
        "round_{n}".format(n=prev_round),
        "{cls}_coverage.json".format(cls=target_class.replace(".java", "")),
    )
    curr_file = os.path.join(
        coverage_data_dir,
        "round_{n}".format(n=round_num),
        "{cls}_coverage.json".format(cls=target_class.replace(".java", "")),
    )

    if not os.path.exists(prev_file):
        return {
            "error": "Previous round file not found: {f}".format(f=prev_file),
            "recommendation": "continue",
            "reason": "First round; no previous data to compare",
        }
    if not os.path.exists(curr_file):
        return {
            "error": "Current round file not found: {f}".format(f=curr_file),
            "recommendation": "stop",
            "reason": "Current round coverage data missing",
        }

    before = load_json(prev_file)
    after = load_json(curr_file)
    return diff_coverage(before, after)


def main():
    parser = argparse.ArgumentParser(
        description="Compare two rounds of coverage data"
    )
    parser.add_argument(
        "files", nargs="*",
        help="Two coverage JSON files: <before.json> <after.json>",
    )
    parser.add_argument(
        "--dir",
        help="Coverage data directory (alternative to positional args)",
    )
    parser.add_argument(
        "--round", type=int,
        help="Round number to diff against previous (used with --dir)",
    )
    parser.add_argument(
        "--class-name", default="Block.java",
        help="Target class name (used with --dir, default: Block.java)",
    )
    parser.add_argument(
        "--min-delta", type=float, default=2.0,
        help="Minimum delta threshold for recommendation (default: 2.0)",
    )
    parser.add_argument(
        "--target-coverage", type=float, default=60.0,
        help="Target coverage percentage (default: 60.0)",
    )
    args = parser.parse_args()

    if args.files and len(args.files) == 2:
        before = load_json(args.files[0])
        after = load_json(args.files[1])
        result = diff_coverage(before, after)
    elif args.dir and args.round:
        result = diff_round_dir(args.dir, args.round, args.class_name)
    else:
        parser.error(
            "Provide either two JSON files or --dir + --round"
        )
        return 1

    result = recommend(
        result,
        min_delta=args.min_delta,
        target_coverage=args.target_coverage,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
