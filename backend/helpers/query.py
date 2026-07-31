"""Query-parameter helpers shared across list endpoints."""


def csv_list(val):
    """Accept either a comma-separated string ("a,b,c") or a list of
    strings from repeated query params. Returns a de-duplicated list
    of trimmed values, or None if the input is empty."""
    if val is None or val == "":
        return None
    if isinstance(val, list):
        out = []
        for v in val:
            if v is None:
                continue
            out.extend(s.strip() for s in str(v).split(",") if s.strip())
    else:
        out = [s.strip() for s in str(val).split(",") if s.strip()]
    if not out:
        return None
    # Preserve order but drop dupes.
    seen = set()
    dedup = []
    for s in out:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    return dedup
