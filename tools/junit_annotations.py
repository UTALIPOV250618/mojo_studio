"""Print failed pytest cases from a JUnit XML file as GitHub Actions error annotations.

Annotations are readable through the public checks API, unlike job logs.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(path: str) -> int:
    count = 0
    for case in ET.parse(path).getroot().iter("testcase"):
        for bad in [*case.findall("failure"), *case.findall("error")]:
            name = f"{case.get('classname')}::{case.get('name')}"
            body = (bad.get("message") or "") + "\n" + (bad.text or "")[-1500:]
            print(f"::error title={_escape(name)}::{_escape(body.strip())}")
            count += 1
            if count >= 10:  # GitHub keeps at most 10 error annotations per step
                return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
