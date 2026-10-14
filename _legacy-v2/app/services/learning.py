import re
from app.services.matcher import normalise

FIELD_PATTERNS = {
    "amount": r"(?P<amount>[₹$€£]?\s*[\d,]+(?:\.\d{1,2})?)",
    "balance": r"(?P<balance>[₹$€£]?\s*[\d,]+(?:\.\d{1,2})?)",
    "reference": r"(?P<reference>[A-Za-z0-9._/-]+)",
    "merchant": r"(?P<merchant>.+?)",
    "direction": r"(?P<direction>[A-Za-z][A-Za-z _-]{1,30})",
}

def build_pattern(text: str, spans: dict[str, tuple[int,int]]) -> tuple[str,int]:
    clean = {k:v for k,v in spans.items() if v and 0 <= v[0] < v[1] <= len(text)}
    occupied = []
    for name,(a,b) in clean.items():
        if any(max(a,x) < min(b,y) for x,y,_ in occupied):
            raise ValueError("Field highlights overlap")
        occupied.append((a,b,name))
    occupied.sort()
    out=[]; pos=0; specificity=0
    for a,b,name in occupied:
        literal=text[pos:a]
        out.append(re.escape(literal))
        specificity += len(literal)
        out.append(FIELD_PATTERNS.get(name, r"(?P<%s>.+?)" % name))
        pos=b
    literal=text[pos:]
    out.append(re.escape(literal))
    specificity += len(literal)
    return "".join(out), specificity

def group_key(text: str) -> str:
    import hashlib
    return hashlib.sha256(normalise(text).encode()).hexdigest()[:32]
