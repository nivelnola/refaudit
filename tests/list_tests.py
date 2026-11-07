"""Write tests/TESTS.txt: every test, by file and section, as a readable sentence.

Run from the project folder after adding tests:  python tests/list_tests.py
"""
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
collected = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT,
                           capture_output=True, text=True).stdout
cases = Counter(re.match(r"[^:]+::(\w+)", line).group(1)
                for line in collected.splitlines() if "::" in line)

lines = [f"refaudit tests: {sum(cases.values())} cases in {len(cases)} tests. Run with: pytest",
         "No test uses the network or the API. (N) = the test runs N cases.", ""]
for path in sorted((ROOT / "tests").glob("test_*.py")):
    lines += [f"{path.name}", "=" * len(path.name)]
    for line in path.read_text(encoding="utf-8").splitlines():
        section = re.match(r"# --- (.+?) -*$", line)
        test = re.match(r"def (test_\w+)\(", line)
        if section:
            lines += ["", section.group(1)]
        elif test:
            name = test.group(1)
            n = cases.get(name, 1)
            sentence = name[len("test_"):].replace("_", " ")
            lines.append(f"  - {sentence}" + (f" ({n})" if n > 1 else ""))
    lines.append("")
(ROOT / "tests" / "TESTS.txt").write_text("\n".join(lines), encoding="utf-8")
print(len(lines), "lines;", sum(cases.values()), "cases")
