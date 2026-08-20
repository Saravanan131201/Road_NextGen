from bson import ObjectId
from datetime import datetime, timezone, timedelta
import json


class MongoJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def serialize_doc(doc: dict) -> dict:
    """Recursively convert MongoDB document to JSON-safe dict."""
    if doc is None:
        return {}
    result = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, dict):
            result[k] = serialize_doc(v)
        elif isinstance(v, list):
            result[k] = [
                serialize_doc(i) if isinstance(i, dict)
                else str(i) if isinstance(i, ObjectId)
                else i
                for i in v
            ]
        else:
            result[k] = v
    return result


def serialize_list(docs: list) -> list:
    return [serialize_doc(d) for d in (docs or [])]


def paginate(data: list, page: int, per_page: int, total: int) -> dict:
    return {
        "data": data,
        "pagination": {
            "page":     page,
            "per_page": per_page,
            "total":    total,
            "pages":    (total + per_page - 1) // per_page
        }
    }


def time_ago(dt) -> str:
    """Human-readable relative time string."""
    if not dt:
        return "Unknown"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return dt[:10]
    try:
        delta = datetime.utcnow() - dt.replace(tzinfo=None)
    except Exception:
        return "Unknown"
    s = int(delta.total_seconds())
    if s < 0:
        return "Just now"
    if s < 60:
        return "Just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    if s < 2592000:
        return f"{s // 86400}d ago"
    if s < 31536000:
        return f"{s // 2592000}mo ago"
    return f"{s // 31536000}y ago"

IST = timezone(timedelta(hours=5, minutes=30))

def _fmt_dt(value) -> str:
    """Format a UTC datetime / ISO string in IST as 'YYYY/MM/DD at HH:MM AM/PM'."""
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    # Convert to IST
    value = value.astimezone(IST)
    return value.strftime("%Y/%m/%d at %I:%M %p")

def get_greeting_hour() -> int:
    """Return current UTC hour for greeting logic."""
    return datetime.now(IST).hour