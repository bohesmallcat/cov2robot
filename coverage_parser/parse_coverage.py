#!/usr/bin/env python3
"""
Parse JaCoCo XML/HTML and LCOV HTML coverage reports into structured JSON.

Usage:
    python parse_coverage.py <report_path_or_url> [--format auto|jacoco-xml|jacoco-html|lcov-html]

Accepts both local file paths and HTTP/HTTPS URLs.  When a URL is given the
file is downloaded to a temporary location before parsing.

Output: JSON to stdout with coverage summary, uncovered methods, lines,
        branches, uncovered_blocks (contiguous gap regions with containing
        method), and partially_covered_methods.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

try:
    from urllib.request import urlopen, Request
    from urllib.error import URLError
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _is_url(path: str) -> bool:
    return path.startswith('http://') or path.startswith('https://')


def _download_to_temp(url: str) -> str:
    """Download *url* to a temporary file and return its path."""
    if not _HAS_URLLIB:
        print(json.dumps({'error': 'urllib not available; cannot download URLs'}),
              file=sys.stderr)
        sys.exit(1)
    req = Request(url, headers={'User-Agent': 'parse_coverage/1.0'})
    try:
        resp = urlopen(req, timeout=60)
    except URLError as exc:
        print(json.dumps({'error': 'Failed to download %s: %s' % (url, exc)}),
              file=sys.stderr)
        sys.exit(1)

    # Preserve the original file extension for format detection
    suffix = '.html'
    if '.' in url.split('/')[-1]:
        suffix = '.' + url.split('/')[-1].rsplit('.', 1)[-1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(resp.read())
    except Exception:
        os.close(fd)
        raise
    return tmp_path


# ---------------------------------------------------------------------------
# Shared: source-line utilities
# ---------------------------------------------------------------------------

# Strip LCOV prefixes from captured source text.
# Format: ":          0 :  <source>" or "[ -  - ]:          0 :  <source>"
# Non-executable lines have ":            :  <source>" (no digit, just spaces).
_LCOV_PREFIX_RE = re.compile(r'^(?:\[[\s+\-#]*\])?\s*:\s*\d*\s*:\s*')

# Match Java method declarations — must begin with access modifier or
# recognised return type.
_METHOD_RE = re.compile(
    r'^\s*'
    r'(?:@\w+\s+)*'                            # optional annotations
    r'(?:(?:public|private|protected)\s+)'
    r'(?:(?:static|final|synchronized|abstract|native)\s+)*'
    r'(?:void|int|long|boolean|float|double|char|byte|short'
    r'|[A-Z][\w.]*(?:<[^>]+>)?(?:\[\])*'
    r')\s+'
    r'(\w+)\s*\('
)

# Match constructors: ClassName(...)
_CTOR_RE = re.compile(
    r'^\s*(?:public|private|protected)\s+([A-Z]\w*)\s*\('
)

# Match inner-class or anonymous-class declarations
_CLASS_RE = re.compile(
    r'^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*class\s+(\w+)'
)

# Words that are never method names
_NOT_METHODS = frozenset({
    'if', 'else', 'for', 'while', 'switch', 'return', 'throw',
    'new', 'catch', 'try', 'finally', 'assert', 'synchronized',
})


def _clean_source(source: str) -> str:
    """Strip LCOV hit-count prefix from a source line."""
    return _LCOV_PREFIX_RE.sub('', source).strip()


def _detect_method_name(source_clean: str):
    """Return a method/constructor name if *source_clean* looks like a decl."""
    m = _METHOD_RE.search(source_clean)
    if not m:
        m = _CTOR_RE.search(source_clean)
    if m and m.group(1) not in _NOT_METHODS:
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Build a method boundary map from ALL source lines
# ---------------------------------------------------------------------------

def _build_method_map(all_lines):
    """Return a sorted list of (start_line, method_name) from *all_lines*.

    *all_lines* is a list of dicts with at least ``line`` and ``source`` keys.
    We scan every line for method/constructor signatures and record the line
    where each one starts.  The list is sorted by start_line ascending.
    """
    methods = []
    for entry in all_lines:
        src = _clean_source(entry.get('source', ''))
        name = _detect_method_name(src)
        if name:
            methods.append((entry['line'], name))
    methods.sort(key=lambda x: x[0])
    return methods


def _find_containing_method(line_no, method_map):
    """Return the name of the method that contains *line_no*, or None."""
    containing = None
    for start, name in method_map:
        if start <= line_no:
            containing = name
        else:
            break
    return containing


# ---------------------------------------------------------------------------
# Group uncovered lines into contiguous blocks
# ---------------------------------------------------------------------------

def _build_uncovered_blocks(uncovered_lines, method_map, gap_tolerance=3):
    """Group consecutive uncovered lines into blocks.

    Lines within *gap_tolerance* of each other are merged (to bridge blank
    lines, comments, or braces between executable statements).

    Each block includes the containing method name (looked up from
    *method_map*) and size.
    """
    if not uncovered_lines:
        return []

    sorted_lines = sorted(uncovered_lines, key=lambda x: x['line'])
    blocks = []
    cur_start = sorted_lines[0]['line']
    cur_end = sorted_lines[0]['line']
    cur_count = 1

    for entry in sorted_lines[1:]:
        ln = entry['line']
        if ln <= cur_end + gap_tolerance:
            cur_end = ln
            cur_count += 1
        else:
            blocks.append(_make_block(cur_start, cur_end, cur_count, method_map))
            cur_start = ln
            cur_end = ln
            cur_count = 1
    blocks.append(_make_block(cur_start, cur_end, cur_count, method_map))

    # Sort largest-first for convenience
    blocks.sort(key=lambda b: -b['uncovered_line_count'])
    return blocks


def _make_block(start, end, count, method_map):
    method = _find_containing_method(start, method_map)
    return {
        'start_line': start,
        'end_line': end,
        'uncovered_line_count': count,
        'containing_method': method,
    }


# ---------------------------------------------------------------------------
# Detect partially-covered methods
# ---------------------------------------------------------------------------

def _detect_method_coverage(all_lines, method_map):
    """Classify each method as covered / uncovered / partial.

    Returns three lists: (covered_methods, uncovered_methods, partially_covered_methods).
    Each entry is a dict with name, start_line, end_line, covered_lines, uncovered_lines.
    """
    if not method_map:
        return [], [], []

    # Build ranges: method i spans from method_map[i].start to method_map[i+1].start-1
    ranges = []
    for idx, (start, name) in enumerate(method_map):
        if idx + 1 < len(method_map):
            end = method_map[idx + 1][0] - 1
        else:
            # Last method extends to the last known line
            max_line = max((l['line'] for l in all_lines), default=start)
            end = max_line
        ranges.append((start, end, name))

    # Build line -> status lookup
    status_map = {}
    for entry in all_lines:
        ln = entry['line']
        st = entry.get('status')
        if st in ('covered', 'uncovered', 'partial'):
            status_map[ln] = st

    covered = []
    uncovered = []
    partial = []

    for start, end, name in ranges:
        cov_count = 0
        uncov_count = 0
        for ln in range(start, end + 1):
            st = status_map.get(ln)
            if st == 'covered' or st == 'partial':
                cov_count += 1
            elif st == 'uncovered':
                uncov_count += 1

        if cov_count == 0 and uncov_count == 0:
            continue  # no executable lines

        entry = {
            'name': name,
            'start_line': start,
            'end_line': end,
            'covered_lines': cov_count,
            'uncovered_lines': uncov_count,
        }

        if uncov_count == 0:
            covered.append(entry)
        elif cov_count == 0:
            uncovered.append(entry)
        else:
            entry['coverage_pct'] = round(cov_count / (cov_count + uncov_count) * 100, 1)
            partial.append(entry)

    # Sort partial by uncovered_lines descending (worst gaps first)
    partial.sort(key=lambda m: -m['uncovered_lines'])
    return covered, uncovered, partial


# ---------------------------------------------------------------------------
# Legacy: group uncovered lines into methods (kept for backward compat)
# ---------------------------------------------------------------------------

def _group_lines_into_methods(uncovered_lines: list, covered_line_nums: list) -> list:
    """Heuristic: group consecutive uncovered lines and try to find method names."""
    methods = []
    current_group = []
    current_method = None

    for uline in uncovered_lines:
        source = uline.get('source', '')
        source_clean = _clean_source(source)
        name = _detect_method_name(source_clean)
        if name:
            if current_group and current_method:
                methods.append({
                    'name': current_method,
                    'start_line': current_group[0]['line'],
                    'end_line': current_group[-1]['line'],
                    'uncovered_line_count': len(current_group),
                })
            current_method = name
            current_group = [uline]
        else:
            current_group.append(uline)

    if current_group and current_method:
        methods.append({
            'name': current_method,
            'start_line': current_group[0]['line'],
            'end_line': current_group[-1]['line'],
            'uncovered_line_count': len(current_group),
        })

    return methods


# ---------------------------------------------------------------------------
# JaCoCo XML Parser
# ---------------------------------------------------------------------------

def parse_jacoco_xml(path: str) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()

    results = []
    for package in root.findall('.//package'):
        pkg_name = package.get('name', '').replace('/', '.')
        for cls in package.findall('class'):
            cls_name = cls.get('name', '').replace('/', '.')
            source_file = cls.get('sourcefilename', '')
            full_source = f"{package.get('name', '')}/{source_file}" if source_file else cls_name

            # Class-level counters
            summary = {}
            for counter in cls.findall('counter'):
                ctype = counter.get('type', '').lower()
                missed = int(counter.get('missed', 0))
                covered = int(counter.get('covered', 0))
                total = missed + covered
                pct = round(covered / total * 100, 1) if total > 0 else 0.0
                summary[f"{ctype}_hit"] = covered
                summary[f"{ctype}_total"] = total
                summary[f"{ctype}_coverage"] = pct

            # Normalize to common keys
            norm_summary = {
                'lines_hit': summary.get('line_hit', 0),
                'lines_total': summary.get('line_total', 0),
                'line_coverage': summary.get('line_coverage', 0.0),
                'branches_hit': summary.get('branch_hit', 0),
                'branches_total': summary.get('branch_total', 0),
                'branch_coverage': summary.get('branch_coverage', 0.0),
                'methods_hit': summary.get('method_hit', 0),
                'methods_total': summary.get('method_total', 0),
                'method_coverage': summary.get('method_coverage', 0.0),
            }

            # Methods
            covered_methods = []
            uncovered_methods = []
            for method in cls.findall('method'):
                m_name = method.get('name', '')
                m_desc = method.get('desc', '')
                m_line = int(method.get('line', 0))
                m_counters = {}
                for counter in method.findall('counter'):
                    ctype = counter.get('type', '').lower()
                    missed = int(counter.get('missed', 0))
                    covered = int(counter.get('covered', 0))
                    m_counters[ctype] = {'missed': missed, 'covered': covered}

                line_info = m_counters.get('line', {'missed': 0, 'covered': 0})
                method_entry = {
                    'name': m_name,
                    'descriptor': m_desc,
                    'line': m_line,
                    'lines_missed': line_info['missed'],
                    'lines_covered': line_info['covered'],
                }
                branch_info = m_counters.get('branch', None)
                if branch_info:
                    method_entry['branches_missed'] = branch_info['missed']
                    method_entry['branches_covered'] = branch_info['covered']

                if line_info['covered'] == 0 and line_info['missed'] > 0:
                    uncovered_methods.append(method_entry)
                else:
                    covered_methods.append(method_entry)

            results.append({
                'file': full_source,
                'class': cls_name,
                'summary': norm_summary,
                'uncovered_methods': uncovered_methods,
                'covered_methods': covered_methods,
                'partially_covered_methods': [],
                'uncovered_lines': [],
                'uncovered_branches': [],
                'uncovered_blocks': [],
            })

    return results[0] if len(results) == 1 else {'classes': results}


# ---------------------------------------------------------------------------
# LCOV HTML Parser
# ---------------------------------------------------------------------------

class LCOVHTMLParser(HTMLParser):
    """Parse LCOV-generated HTML coverage reports."""

    def __init__(self):
        super().__init__()
        self.in_source_pre = False
        self.current_line = 0
        self.lines = []  # list of (line_no, status, branch_status, source_text)
        self._collecting_text = False
        self._current_text = ''
        self._current_class = ''
        self._in_line_num = False
        self._line_status = None
        self._branch_status = None
        self._source_text = ''
        self._in_header_value = False
        self._in_header_item = False
        self._header_item = ''
        self.title = ''
        self.summary = {}
        self._pending_key = ''

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get('class', '')

        if tag == 'pre' and 'source' in cls:
            self.in_source_pre = True

        if tag == 'span':
            if 'lineNum' in cls:
                self._in_line_num = True
                self._current_text = ''
            elif 'lineCov' in cls:
                self._line_status = 'covered'
                self._current_text = ''
                self._collecting_text = True
            elif 'lineNoCov' in cls:
                self._line_status = 'uncovered'
                self._current_text = ''
                self._collecting_text = True
            elif 'branchCov' in cls:
                self._branch_status = 'covered'
            elif 'branchNoCov' in cls:
                self._branch_status = 'uncovered'

        if tag == 'td':
            if 'headerItem' in cls:
                self._in_header_item = True
                self._current_text = ''
            elif 'headerCovTableEntry' in cls or 'headerCovTableEntryLo' in cls or 'headerCovTableEntryMed' in cls or 'headerCovTableEntryHi' in cls:
                self._in_header_value = True
                self._current_text = ''

        if tag == 'title':
            self._collecting_text = True
            self._current_text = ''

    def handle_endtag(self, tag):
        if tag == 'span' and self._in_line_num:
            self._in_line_num = False
            try:
                self.current_line = int(self._current_text.strip())
            except ValueError:
                pass

        if tag == 'title':
            self.title = self._current_text.strip()
            self._collecting_text = False

        if tag == 'a' and self.in_source_pre:
            # End of a source line
            self.lines.append({
                'line': self.current_line,
                'status': self._line_status,
                'branch': self._branch_status,
                'source': self._source_text.strip() if self._source_text else '',
            })
            self._line_status = None
            self._branch_status = None
            self._source_text = ''
            self._collecting_text = False

        if tag == 'td':
            if self._in_header_item:
                self._in_header_item = False
                self._pending_key = self._current_text.strip().rstrip(':').lower()
            elif self._in_header_value:
                self._in_header_value = False
                val = self._current_text.strip()
                if self._pending_key:
                    self.summary[self._pending_key] = val
                    self._pending_key = ''

    def handle_data(self, data):
        if self._in_line_num:
            self._current_text += data
        if self._collecting_text:
            self._current_text += data
        if self.in_source_pre and self._line_status:
            self._source_text += data
        # Also capture source for non-hit lines (status is None but we are in source pre)
        if self.in_source_pre and self._line_status is None and not self._in_line_num:
            self._source_text += data
        if self._in_header_item or self._in_header_value:
            self._current_text += data
        if self._collecting_text:
            self._current_text += data


def parse_lcov_html(path: str) -> dict:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    parser = LCOVHTMLParser()
    parser.feed(content)

    # Extract file name from title
    file_name = ''
    if parser.title:
        parts = parser.title.split(' - ')
        if len(parts) >= 2:
            file_name = parts[-1].strip()

    # Build coverage data
    uncovered_lines = []
    uncovered_branches = []
    covered_lines = []
    total_lines = 0
    hit_lines = 0
    total_branches = 0
    hit_branches = 0

    for line in parser.lines:
        if line['status'] == 'uncovered':
            total_lines += 1
            uncovered_lines.append({
                'line': line['line'],
                'source': line['source'],
            })
        elif line['status'] == 'covered':
            total_lines += 1
            hit_lines += 1
            covered_lines.append(line['line'])

        if line['branch'] == 'uncovered':
            total_branches += 1
            uncovered_branches.append({
                'line': line['line'],
                'source': line['source'],
            })
        elif line['branch'] == 'covered':
            total_branches += 1
            hit_branches += 1

    # Try to extract summary from header
    lines_hit = hit_lines
    lines_total = total_lines
    branches_hit = hit_branches
    branches_total = total_branches

    for key, val in parser.summary.items():
        try:
            num = int(val)
            if key == 'lines' and 'lines_total' not in locals():
                pass  # use computed
        except ValueError:
            pass

    line_coverage = round(lines_hit / lines_total * 100, 1) if lines_total > 0 else 0.0
    branch_coverage = round(branches_hit / branches_total * 100, 1) if branches_total > 0 else 0.0

    # Build method map from ALL lines (covered + uncovered + non-executable)
    method_map = _build_method_map(parser.lines)

    # Group uncovered lines into contiguous blocks with method context
    uncovered_blocks = _build_uncovered_blocks(uncovered_lines, method_map)

    # Classify methods into covered / uncovered / partially-covered
    cov_methods, uncov_methods, partial_methods = _detect_method_coverage(
        parser.lines, method_map)

    # Legacy: fully-uncovered methods via original heuristic (kept for compat)
    legacy_uncovered = _group_lines_into_methods(uncovered_lines, covered_lines)

    summary = {
        'lines_hit': lines_hit,
        'lines_total': lines_total,
        'line_coverage': line_coverage,
        'branches_hit': branches_hit,
        'branches_total': branches_total,
        'branch_coverage': branch_coverage,
    }

    return {
        'file': file_name,
        'summary': summary,
        'uncovered_methods': uncov_methods if uncov_methods else legacy_uncovered,
        'covered_methods': cov_methods,
        'partially_covered_methods': partial_methods,
        'uncovered_blocks': uncovered_blocks,
        'uncovered_lines': uncovered_lines,
        'uncovered_branches': uncovered_branches,
    }


# ---------------------------------------------------------------------------
# JaCoCo HTML Parser
# ---------------------------------------------------------------------------

class JaCoCoHTMLParser(HTMLParser):
    """Parse JaCoCo-generated HTML coverage reports (single class view)."""

    def __init__(self):
        super().__init__()
        self.in_source = False
        self.lines = []
        self._current_line = 0
        self._current_class = ''
        self._current_text = ''
        self._in_span = False
        self.title = ''
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get('class', '')
        elem_id = attrs_dict.get('id', '')

        if tag == 'title':
            self._in_title = True
            self._current_text = ''

        if tag == 'span' and cls in ('fc', 'nc', 'pc'):
            self._current_class = cls
            self._in_span = True
            self._current_text = ''

        if tag == 'span' and elem_id.startswith('L'):
            try:
                self._current_line = int(elem_id[1:])
            except ValueError:
                pass

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
            self.title = self._current_text.strip()

        if tag == 'span' and self._in_span:
            self._in_span = False
            status_map = {'fc': 'covered', 'nc': 'uncovered', 'pc': 'partial'}
            self.lines.append({
                'line': self._current_line,
                'status': status_map.get(self._current_class, 'unknown'),
                'source': self._current_text.strip(),
            })
            self._current_class = ''

    def handle_data(self, data):
        if self._in_title:
            self._current_text += data
        if self._in_span:
            self._current_text += data


def parse_jacoco_html(path: str) -> dict:
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    parser = JaCoCoHTMLParser()
    parser.feed(content)

    uncovered_lines = []
    partial_lines = []
    covered_lines = []

    for line in parser.lines:
        if line['status'] == 'uncovered':
            uncovered_lines.append({'line': line['line'], 'source': line['source']})
        elif line['status'] == 'partial':
            partial_lines.append({'line': line['line'], 'source': line['source']})
        elif line['status'] == 'covered':
            covered_lines.append(line['line'])

    total = len(uncovered_lines) + len(partial_lines) + len(covered_lines)
    hit = len(covered_lines) + len(partial_lines)
    pct = round(hit / total * 100, 1) if total > 0 else 0.0

    # Build method map from ALL lines
    method_map = _build_method_map(parser.lines)

    # Uncovered blocks with method context
    uncovered_blocks = _build_uncovered_blocks(uncovered_lines, method_map)

    # Method coverage classification
    cov_methods, uncov_methods, partial_methods = _detect_method_coverage(
        parser.lines, method_map)

    # Legacy fallback
    legacy_uncovered = _group_lines_into_methods(uncovered_lines, [l for l in covered_lines])

    return {
        'file': parser.title or Path(path).stem,
        'summary': {
            'lines_hit': hit,
            'lines_total': total,
            'line_coverage': pct,
        },
        'uncovered_methods': uncov_methods if uncov_methods else legacy_uncovered,
        'covered_methods': cov_methods,
        'partially_covered_methods': partial_methods,
        'uncovered_blocks': uncovered_blocks,
        'uncovered_lines': uncovered_lines,
        'uncovered_branches': partial_lines,
    }


# ---------------------------------------------------------------------------
# Format detection & main
# ---------------------------------------------------------------------------

def detect_format(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == '.xml':
        return 'jacoco-xml'
    if ext in ('.html', '.htm'):
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(2048)
        if 'LCOV' in head or 'lcov' in head or 'gcov.css' in head:
            return 'lcov-html'
        if 'JaCoCo' in head or 'jacoco' in head.lower():
            return 'jacoco-html'
        # Fallback: check for LCOV-style line markers
        if 'lineCov' in head or 'lineNoCov' in head:
            return 'lcov-html'
        if '"fc"' in head or '"nc"' in head or '"pc"' in head:
            return 'jacoco-html'
    return 'unknown'


def main():
    parser = argparse.ArgumentParser(description='Parse coverage reports into JSON')
    parser.add_argument('report', help='Path or URL to coverage report file')
    parser.add_argument('--format', choices=['auto', 'jacoco-xml', 'jacoco-html', 'lcov-html'],
                        default='auto', help='Report format (default: auto-detect)')
    args = parser.parse_args()

    # Download if URL
    report_path = args.report
    tmp_path = None
    if _is_url(report_path):
        tmp_path = _download_to_temp(report_path)
        report_path = tmp_path

    try:
        if not os.path.exists(report_path):
            print(json.dumps({'error': 'File not found: %s' % args.report}),
                  file=sys.stderr)
            sys.exit(1)

        fmt = args.format
        if fmt == 'auto':
            fmt = detect_format(report_path)
            if fmt == 'unknown':
                print(json.dumps({'error': 'Could not detect format. Use --format to specify.'}),
                      file=sys.stderr)
                sys.exit(1)

        if fmt == 'jacoco-xml':
            result = parse_jacoco_xml(report_path)
        elif fmt == 'jacoco-html':
            result = parse_jacoco_html(report_path)
        elif fmt == 'lcov-html':
            result = parse_lcov_html(report_path)
        else:
            print(json.dumps({'error': 'Unsupported format: %s' % fmt}),
                  file=sys.stderr)
            sys.exit(1)

        print(json.dumps(result, indent=2))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == '__main__':
    main()
