"""Writes installer/version.inc from lavati.__version__, so the version is only ever edited in lavati/__init__.py."""
import os
import re

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(root, "lavati", "__init__.py"), encoding="utf-8") as f:
    version = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)
with open(os.path.join(root, "installer", "version.inc"), "w", encoding="utf-8") as f:
    f.write('#define AppVersion "%s"\n' % version)
print("installer version", version)
