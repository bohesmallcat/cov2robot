"""Debug helper: run parse_coverage.py on a report and show uncovered methods."""
import subprocess, json, os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARSER = os.path.join(SCRIPT_DIR, "parse_coverage.py")

if len(sys.argv) < 2:
    print("Usage: python check_methods.py <path-to-report>")
    sys.exit(1)

result = subprocess.run(
    [sys.executable, PARSER, sys.argv[1]],
    capture_output=True, text=True
)
data = json.loads(result.stdout)
print("Methods found: {}".format(len(data['uncovered_methods'])))
for m in data['uncovered_methods']:
    print("  {} lines {}-{} ({} lines)".format(
        m['name'], m['start_line'], m['end_line'], m['uncovered_line_count']))
