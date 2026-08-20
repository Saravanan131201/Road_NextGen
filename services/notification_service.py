from flask import current_app
from extensions import mongo, socketio
from datetime import datetime
from bson import ObjectId


def _emit_notification(notif):
    """
    Emit a notification over Socket.IO to the user's personal room.
    The notif dict must already be serialized (no ObjectId / datetime objects).
    """
    user_id = notif.get("user_id")
    if not user_id:
        return
    payload = {
        "title":      notif.get("title", ""),
        "message":    notif.get("message", ""),
        "type":       notif.get("type", "info"),
        "data":       notif.get("data", {}),
        "created_at": notif.get("created_at", ""),
        "is_read":    False,
    }
    socketio.emit("new_notification", payload, room=str(user_id))

    

def create_notification(user_id, title, message, notif_type="info", data=None):
    """Store notification in MongoDB and push it live via Socket.IO."""
    now = datetime.utcnow()
    notif = {
        "user_id":    str(user_id),
        "title":      title,
        "message":    message,
        "type":       notif_type,
        "data":       data or {},
        "is_read":    False,
        "created_at": now,
    }
    mongo.db.notifications.insert_one(notif)

    # Emit with serializable created_at
    _emit_notification({**notif, "created_at": now.isoformat()})
    return notif


def upsert_notification(user_id, ref_key: str, title: str, message: str,
                        notif_type: str = "info", data: dict = None):
    """
    Create a notification if none with ref_key exists for this user,
    otherwise update the existing one in-place (title, message, timestamp).
    This prevents duplicate notifications when e.g. workers are reassigned.
    """
        

    now = datetime.utcnow()
    result = mongo.db.notifications.update_one(
        {"user_id": str(user_id), "ref_key": ref_key},
        {"$set": {
            "title":      title,
            "message":    message,
            "type":       notif_type,
            "data":       data or {},
            "is_read": False,
            "updated_at": now,
        },
         "$setOnInsert": {
            "user_id":    str(user_id),
            "ref_key":    ref_key,
            "created_at": now,
        }},
        upsert=True
    )

    if result.upserted_id is not None:
        _emit_notification({
            "user_id":    str(user_id),
            "title":      title,
            "message":    message,
            "type":       notif_type,
            "data":       data or {},
            "created_at": now.isoformat(),
        })



def get_user_notifications(user_id, limit=20):
    return list(mongo.db.notifications.find(
        {"user_id": str(user_id)}
    ).sort("created_at", -1).limit(limit))

def mark_read(notif_id):
    mongo.db.notifications.update_one(
        {"_id": ObjectId(notif_id)},
        {"$set": {"is_read": True}}
    )

def mark_all_read(user_id):
    mongo.db.notifications.update_many(
        {"user_id": str(user_id)},
        {"$set": {"is_read": True}}
    )