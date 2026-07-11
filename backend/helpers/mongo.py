from bson import ObjectId


def oid_or_none(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def to_public_doc(doc, *, drop=()):
    if not doc:
        return None
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    for k in drop:
        out.pop(k, None)
    return out
