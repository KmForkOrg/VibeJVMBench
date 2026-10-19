#!/usr/bin/env python3

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from export import validate

MAX_ERRORS = 15


def check(path, folder):
    try:
        doc = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return [f"not readable JSON: {e}"]
    errors = validate(doc)
    target = doc.get("target") if isinstance(doc, dict) else None
    name = target.get("name") if isinstance(target, dict) else None
    if name and name != folder:
        errors.append(f"$.target.name: {name!r} does not match its folder jvms/{folder}/")
    return errors


def main(argv):
    failed = False
    for shown, local in zip(argv[::2], argv[1::2]):
        errors = check(Path(local), Path(shown).parent.name)
        if not errors:
            continue
        failed = True
        print(f"- `{shown}`")
        for error in errors[:MAX_ERRORS]:
            print(f"  - `{error}`")
        if len(errors) > MAX_ERRORS:
            print(f"  - …and {len(errors) - MAX_ERRORS} more")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
