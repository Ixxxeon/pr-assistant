"""CLI adapters returning {result, usage}; no GitHub operations belong here."""

import re


class ReviewError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def failed(message):
    """Classify provider errors, never ordinary review text mentioning rate limits."""
    limited = re.search(r"usage.limit|rate.limit|quota.exceeded|hit your.*limit|limit.*reached|too many requests",
                        message, re.I | re.S)
    auth = re.search(r"failed to authenticate|authentication (?:failed|required)|not logged in|"
                     r"invalid api key|oauth.*expired", message, re.I)
    return ReviewError(message or "review process failed", 3 if limited else 4 if auth else 1)


def check_final_text(result):
    """Some CLIs wrap startup/auth errors in a successful agent-message envelope."""
    if not isinstance(result, str) or not result.strip():
        raise ReviewError("reviewer returned no final review")
    if re.match(r"\s*(?:failed to authenticate\b|authentication (?:failed|required)\b|"
                r"not logged in\b|invalid api key\b|oauth (?:session|token) (?:has )?expired\b|"
                r"(?:error:\s*)?unknown (?:slash )?command\b|unknown skill\b|"
                r"you(?:'ve| have)? hit your .*limit\b|usage limit (?:reached|exceeded)\b)", result, re.I):
        raise failed(result)
