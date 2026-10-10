"""Configuration comparison helpers.

Produces unified diff lines, side-by-side rows and change statistics, with
optional normalisation so that noise (timestamps, trailing spaces) does not
show up as a configuration change.
"""
import re
from difflib import SequenceMatcher, unified_diff

# Lines that change on every backup without a real configuration change.
VOLATILE_PATTERNS = [
    r"^\s*!\s*Time:",                                  # Arista
    r"^\s*!\s*Command:\s*show running-config",         # Arista
    r"^\s*!\s*device:\s*",                             # Arista header
    r"^\s*!\s*boot system",                            # Arista header
    r"^\s*!\s*Last configuration was",                 # Huawei
    r"^\s*!\s*Software Version",                       # Huawei
    r"^\s*##\s*Last (commit|changed)",                 # Juniper
    r"^\s*set version\s",                              # Juniper (display set)
    r"^\s*(\S+[>#\]])\s*$",                            # bare CLI prompt
    r"^\s*(<[^>]+>|\[[^\]]+\])\s*$",                   # Huawei prompt <SW> / [SW]
    r"^\s*(display current-configuration|show configuration.*|screen-length 0 temporary)\s*$",
]
_VOLATILE_RE = [re.compile(p, re.IGNORECASE) for p in VOLATILE_PATTERNS]


def normalise(lines, ignore_whitespace=False, ignore_volatile=False):
    """Return (kept_lines, original_index_map)."""
    out, index = [], []
    for i, line in enumerate(lines):
        if ignore_volatile and any(r.search(line) for r in _VOLATILE_RE):
            continue
        out.append(" ".join(line.split()) if ignore_whitespace else line.rstrip())
        index.append(i)
    return out, index


def compare(text_a: str, text_b: str, *, ignore_whitespace=False, ignore_volatile=True,
            label_a="A", label_b="B", context=3):
    raw_a = text_a.splitlines()
    raw_b = text_b.splitlines()
    a, _ = normalise(raw_a, ignore_whitespace, ignore_volatile)
    b, _ = normalise(raw_b, ignore_whitespace, ignore_volatile)

    unified = list(unified_diff(a, b, fromfile=label_a, tofile=label_b, lineterm="", n=context))

    rows, added, removed, modified = [], 0, 0, 0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"t": "eq", "ln": i1 + k + 1, "rn": j1 + k + 1, "l": a[i1 + k], "r": b[j1 + k]})
        elif tag == "delete":
            removed += i2 - i1
            for k in range(i1, i2):
                rows.append({"t": "del", "ln": k + 1, "rn": None, "l": a[k], "r": None})
        elif tag == "insert":
            added += j2 - j1
            for k in range(j1, j2):
                rows.append({"t": "add", "ln": None, "rn": k + 1, "l": None, "r": b[k]})
        else:  # replace
            n = max(i2 - i1, j2 - j1)
            removed += i2 - i1
            added += j2 - j1
            modified += min(i2 - i1, j2 - j1)
            for k in range(n):
                li, rj = i1 + k, j1 + k
                rows.append({
                    "t": "mod" if li < i2 and rj < j2 else ("del" if li < i2 else "add"),
                    "ln": li + 1 if li < i2 else None,
                    "rn": rj + 1 if rj < j2 else None,
                    "l": a[li] if li < i2 else None,
                    "r": b[rj] if rj < j2 else None,
                })

    return {
        "changed": bool(added or removed),
        "stats": {
            "added": added,
            "removed": removed,
            "modified": modified,
            "lines_a": len(raw_a),
            "lines_b": len(raw_b),
            "similarity": round(matcher.ratio() * 100, 1) if (a or b) else 100.0,
        },
        "lines": unified,
        "rows": rows,
    }


def quick_stats(text_a: str, text_b: str, ignore_volatile=True):
    """Cheap added/removed counts for timelines."""
    a, _ = normalise(text_a.splitlines(), False, ignore_volatile)
    b, _ = normalise(text_b.splitlines(), False, ignore_volatile)
    added = removed = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            removed += i2 - i1
        if tag in ("insert", "replace"):
            added += j2 - j1
    return added, removed


def config_fingerprint(text: str) -> str:
    """SHA-256 of the configuration without volatile lines (timestamps, prompts,
    trailing spaces). Two backups with the same fingerprint have the same config."""
    import hashlib
    kept, _ = normalise(text.splitlines(), ignore_whitespace=False, ignore_volatile=True)
    while kept and not kept[-1].strip():
        kept.pop()
    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()
