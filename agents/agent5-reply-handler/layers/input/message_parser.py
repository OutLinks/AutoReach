"""
Message parser (Input Layer #2).

Strips the noise out of a raw reply so the understanding layer sees only what the
lead actually wrote:
  - quoted text ("On <date>, X wrote:" blocks and ">"-prefixed lines),
  - common signature delimiters ("-- ", "Sent from my iPhone", etc.),
  - trailing whitespace.

Rule-based and fast — no LLM. Conservative: if stripping would empty the message,
we keep the original.
"""

from __future__ import annotations

import re

_QUOTE_HEADER = re.compile(
    r"^\s*On .+ wrote:\s*$|^\s*-+\s*Original Message\s*-+\s*$|^\s*From:\s.+$",
    re.IGNORECASE,
)
_SIGNATURE_MARKERS = (
    "\n-- \n", "\nSent from my iPhone", "\nSent from my Android",
    "\nGet Outlook for", "\nBest regards", "\nKind regards", "\nThanks,\n",
)


def parse(raw: str) -> str:
    """Return the cleaned reply body."""
    if not raw:
        return ""

    lines = raw.replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    for line in lines:
        if _QUOTE_HEADER.match(line):
            break  # everything below a quote header is quoted history
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)

    body = "\n".join(kept)

    # Trim at the earliest signature marker.
    lowered = body
    cut = len(body)
    for marker in _SIGNATURE_MARKERS:
        idx = lowered.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    body = body[:cut].strip()

    return body or raw.strip()
