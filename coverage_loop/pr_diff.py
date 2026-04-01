#!/usr/bin/env python3
"""
PR Diff Extraction and Java Class Mapping.

Extracts changed Java files from a GitHub Enterprise PR or local git diff,
then maps file paths to fully-qualified Java class names for targeted
coverage analysis.

GHE API calls are delegated to the **github-api** skill when available.
Falls back to a lightweight built-in implementation otherwise.

Supports two modes:
  1. **GitHub Enterprise API** -- fetch PR diff via github-api (get-pr-files / get-pr-diff)
  2. **Local git diff** -- run ``git diff base..head`` on a local clone

Usage (standalone)::

    # From a PR URL
    python pr_diff.py --pr-url https://github.example.com/org/storage-service/pull/123

    # From a local git repo
    python pr_diff.py --repo /path/to/storage --base origin/master --head origin/feature

    # Output JSON
    python pr_diff.py --pr-url ... --json
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys

log = logging.getLogger("pr_diff")

# ---------------------------------------------------------------------------
# Try to import github-api helpers; fall back to built-in if unavailable
# ---------------------------------------------------------------------------

_github_helper_available = False

# Optional: if a github-api helper library is on PYTHONPATH, use its
# token-loading and diff-fetching functions.  Otherwise fall back to
# the built-in implementation below.
try:
    from github_common import load_github_token as _load_token_ext  # noqa: F401
    _github_helper_available = True
    log.debug("External github helper loaded")
except ImportError:
    log.debug("No external github helper; using built-in GHE client")

_get_diff_fn = None
if _github_helper_available:
    try:
        from github_read import get_diff as _get_diff_fn  # noqa: F811
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# PR URL parsing
# ---------------------------------------------------------------------------

_PR_URL_RE = re.compile(
    r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)",
)


def parse_pr_url(url):
    """Parse ``owner/repo`` and PR number from a GHE pull request URL.

    Returns (repo, pr_number) or raises ValueError.
    """
    m = _PR_URL_RE.search(url)
    if not m:
        raise ValueError(
            "Cannot parse PR URL: {u}  "
            "(expected https://host/owner/repo/pull/NUMBER)".format(u=url)
        )
    return m.group(1), int(m.group(2))


# ---------------------------------------------------------------------------
# Built-in GitHub API helpers (used when external helper is not importable)
# ---------------------------------------------------------------------------

_GITHUB_BASE = "https://github.example.com"

try:
    import requests as _requests_mod
except ImportError:
    _requests_mod = None


def _load_github_token():
    """Auto-discover GitHub token.

    Delegates to an external helper's ``load_github_token`` when available,
    otherwise searches ``GITHUB_TOKEN`` env var and ``.env`` files.
    """
    if _github_helper_available:
        try:
            return _load_token_ext()
        except Exception:
            pass  # Fall through to manual search

    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token

    for search_dir in (".", os.path.dirname(os.path.abspath(__file__)) + "/../../.."):
        for env_name in (".env", ".tokens.env"):
            env_file = os.path.join(search_dir, env_name)
            if os.path.isfile(env_file):
                with open(env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GITHUB_TOKEN="):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _builtin_fetch_pr_files(repo, pr_number, token=None, base_url=None):
    """Fetch changed files from GHE PR (built-in, no github-api dependency)."""
    if _requests_mod is None:
        raise RuntimeError("requests library required for GHE API access")

    if not token:
        token = _load_github_token()
    if not token:
        raise RuntimeError("No GITHUB_TOKEN found")

    base = (base_url or _GITHUB_BASE).rstrip("/")
    url = "{base}/api/v3/repos/{repo}/pulls/{pr}/files".format(
        base=base, repo=repo, pr=pr_number,
    )
    headers = {
        "Authorization": "token {t}".format(t=token),
        "Accept": "application/vnd.github.v3+json",
    }

    all_files = []
    page = 1
    while True:
        resp = _requests_mod.get(
            url, headers=headers, verify=False,
            params={"per_page": 100, "page": page}, timeout=30,
        )
        if resp.status_code == 404:
            raise RuntimeError(
                "PR not found or no access: {r}#{n} (HTTP 404). "
                "Check your token has 'repo' scope for {r}.".format(
                    r=repo, n=pr_number,
                )
            )
        resp.raise_for_status()
        files = resp.json()
        if not files:
            break
        all_files.extend(files)
        page += 1
    return all_files


def _builtin_fetch_pr_diff(repo, pr_number, token=None, base_url=None):
    """Fetch unified diff text from GHE PR (built-in)."""
    if _requests_mod is None:
        raise RuntimeError("requests library required")
    if not token:
        token = _load_github_token()
    if not token:
        raise RuntimeError("No GITHUB_TOKEN found")

    base = (base_url or _GITHUB_BASE).rstrip("/")
    url = "{base}/api/v3/repos/{repo}/pulls/{pr}".format(
        base=base, repo=repo, pr=pr_number,
    )
    headers = {
        "Authorization": "token {t}".format(t=token),
        "Accept": "application/vnd.github.v3.diff",
    }
    resp = _requests_mod.get(url, headers=headers, verify=False, timeout=60)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Unified API fetch (prefers github-api, falls back to built-in)
# ---------------------------------------------------------------------------

def fetch_pr_files_api(repo, pr_number, token=None, base_url=None):
    """Fetch changed files from a GHE PR.

    Uses github-api ``github_common`` helpers for auth when available.
    """
    return _builtin_fetch_pr_files(repo, pr_number, token=token, base_url=base_url)


def fetch_pr_diff_text(repo, pr_number, token=None, base_url=None):
    """Fetch unified diff text from a GHE PR.

    Uses github-api ``github_common`` helpers for auth when available.
    """
    return _builtin_fetch_pr_diff(repo, pr_number, token=token, base_url=base_url)


# ---------------------------------------------------------------------------
# Local git diff
# ---------------------------------------------------------------------------

def fetch_changed_files_git(repo_path, base_ref, head_ref):
    """Get changed files from a local git repo via ``git diff --name-status``.

    Parameters
    ----------
    repo_path : str
        Path to the git repository root.
    base_ref : str
        Base ref (e.g. ``origin/master``).
    head_ref : str
        Head ref (e.g. ``origin/feature-branch``).

    Returns
    -------
    list of dict
        Each dict has ``filename``, ``status`` (A/M/D/R).
    """
    cmd = [
        "git", "-C", repo_path,
        "diff", "--name-status", "--diff-filter=AMDR",
        "{base}...{head}".format(base=base_ref, head=head_ref),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            "git diff failed: {err}".format(err=result.stderr.strip())
        )

    files = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status_code = parts[0][0]  # A, M, D, R
            filename = parts[1]
            status_map = {"A": "added", "M": "modified", "D": "removed", "R": "renamed"}
            files.append({
                "filename": filename,
                "status": status_map.get(status_code, status_code),
            })
    return files


def fetch_diff_text_git(repo_path, base_ref, head_ref):
    """Get unified diff text from local git."""
    cmd = [
        "git", "-C", repo_path,
        "diff", "{base}...{head}".format(base=base_ref, head=head_ref),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            "git diff failed: {err}".format(err=result.stderr.strip())
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Java file -> FQN mapping
# ---------------------------------------------------------------------------

# Standard Maven/Gradle source root patterns
_SRC_ROOT_PATTERNS = [
    "src/main/java/",
    "src/test/java/",
    "main/java/",
    "test/java/",
]


def java_file_to_fqn(filepath):
    """Convert a Java source file path to a fully-qualified class name.

    Examples::

        >>> java_file_to_fqn("object-engine/src/main/java/com/example/storage/data/Block.java")
        'com.example.storage.data.Block'
        >>> java_file_to_fqn("build.gradle")  # non-Java
    """
    if not filepath.endswith(".java"):
        return None

    # Strip everything up to and including a known source root
    for pattern in _SRC_ROOT_PATTERNS:
        idx = filepath.find(pattern)
        if idx >= 0:
            rel = filepath[idx + len(pattern):]
            return rel.replace("/", ".").replace(".java", "")

    # Fallback: use everything after last "java/" segment
    parts = filepath.replace("\\", "/").split("/")
    try:
        java_idx = len(parts) - 1 - parts[::-1].index("java")
        rel_parts = parts[java_idx + 1:]
        if rel_parts:
            return ".".join(rel_parts).replace(".java", "")
    except ValueError:
        pass

    # Last resort: derive from filename itself
    return filepath.replace("/", ".").replace(".java", "").rsplit(".", 1)[-1] if "/" in filepath else None


def extract_changed_classes(files):
    """Extract set of Java FQNs from a list of changed files.

    Parameters
    ----------
    files : list of dict
        Each dict must have ``filename`` key.

    Returns
    -------
    set of str
        Fully-qualified Java class names.
    """
    fqns = set()
    for f in files:
        fname = f.get("filename", "")
        if f.get("status") == "removed":
            continue  # Deleted files don't need coverage
        fqn = java_file_to_fqn(fname)
        if fqn:
            fqns.add(fqn)
    return fqns


def extract_changed_lines(diff_text):
    """Parse unified diff text to get per-file changed line numbers.

    Returns dict mapping file path -> set of changed line numbers (new side).
    """
    changed = {}
    current_file = None
    line_num = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            changed.setdefault(current_file, set())
        elif line.startswith("@@ "):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            m = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if m:
                line_num = int(m.group(1)) - 1  # Will be incremented
        elif current_file is not None:
            if line.startswith("+") and not line.startswith("+++"):
                line_num += 1
                changed[current_file].add(line_num)
            elif line.startswith("-"):
                pass  # Removed lines don't affect new line numbers
            else:
                line_num += 1

    return changed


# ---------------------------------------------------------------------------
# High-level: get PR context for coverage analysis
# ---------------------------------------------------------------------------

def get_pr_context(pr_url=None, repo_path=None, base_ref=None, head_ref=None,
                   token=None, ghe_base_url=None):
    """Get PR context: changed classes, changed lines, and metadata.

    Supports both GHE API and local git.  At least one of ``pr_url`` or
    ``(repo_path, base_ref, head_ref)`` must be provided.

    Returns
    -------
    dict
        Keys: ``changed_classes`` (set), ``changed_files`` (list),
        ``changed_lines`` (dict), ``pr_info`` (dict or None).
    """
    changed_files = []
    diff_text = ""
    pr_info = None

    if pr_url:
        repo, pr_number = parse_pr_url(pr_url)
        log.info("Fetching PR %s#%d from GHE API", repo, pr_number)

        try:
            changed_files = fetch_pr_files_api(
                repo, pr_number, token=token, base_url=ghe_base_url,
            )
            diff_text = fetch_pr_diff_text(
                repo, pr_number, token=token, base_url=ghe_base_url,
            )
            pr_info = {
                "repo": repo,
                "pr_number": pr_number,
                "source": "external_helper" if _github_helper_available else "builtin_api",
            }
        except Exception as exc:
            log.warning("GHE API failed: %s", exc)
            if repo_path:
                log.info("Falling back to local git diff")
            else:
                raise

    if not changed_files and repo_path and base_ref and head_ref:
        log.info(
            "Using local git diff: %s (%s...%s)",
            repo_path, base_ref, head_ref,
        )
        changed_files = fetch_changed_files_git(repo_path, base_ref, head_ref)
        diff_text = fetch_diff_text_git(repo_path, base_ref, head_ref)
        pr_info = {
            "repo_path": repo_path,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "source": "local_git",
        }

    # Extract Java class FQNs
    changed_classes = extract_changed_classes(changed_files)

    # Extract per-file changed lines
    changed_lines = extract_changed_lines(diff_text) if diff_text else {}

    java_files = [f for f in changed_files if f.get("filename", "").endswith(".java")]
    non_java = len(changed_files) - len(java_files)

    log.info(
        "PR diff: %d files changed (%d Java, %d non-Java), %d classes identified",
        len(changed_files), len(java_files), non_java, len(changed_classes),
    )
    for fqn in sorted(changed_classes):
        log.info("  %s", fqn)

    return {
        "changed_classes": changed_classes,
        "changed_files": changed_files,
        "changed_lines": changed_lines,
        "pr_info": pr_info,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract changed Java classes from a PR diff",
    )
    parser.add_argument(
        "--pr-url",
        help="GitHub Enterprise PR URL "
             "(e.g. https://github.example.com/org/storage-service/pull/123)",
    )
    parser.add_argument(
        "--repo",
        help="Local git repository path (for local diff mode)",
    )
    parser.add_argument(
        "--base", default="origin/master",
        help="Base branch/ref (default: origin/master)",
    )
    parser.add_argument(
        "--head",
        help="Head branch/ref (default: current branch)",
    )
    parser.add_argument(
        "--json", dest="output_json", action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--token",
        help="GitHub PAT (overrides env / .env)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    ctx = get_pr_context(
        pr_url=args.pr_url,
        repo_path=args.repo,
        base_ref=args.base,
        head_ref=args.head,
        token=args.token,
    )

    if args.output_json:
        out = {
            "changed_classes": sorted(ctx["changed_classes"]),
            "changed_files": [
                {"filename": f["filename"], "status": f.get("status", "?")}
                for f in ctx["changed_files"]
                if f.get("filename", "").endswith(".java")
            ],
            "pr_info": ctx["pr_info"],
        }
        print(json.dumps(out, indent=2))
    else:
        print("Changed Java classes ({n}):".format(
            n=len(ctx["changed_classes"]),
        ))
        for fqn in sorted(ctx["changed_classes"]):
            print("  {fqn}".format(fqn=fqn))

    return 0


if __name__ == "__main__":
    sys.exit(main())
