"""Run once: python setup_indexes.py"""
from pymongo import MongoClient, GEOSPHERE, ASCENDING, DESCENDING

client = MongoClient("mongodb://localhost:27017/roadvision")
db = client.roadvision

# Geospatial
db.reports.create_index([("location", GEOSPHERE)])

# Reports
db.reports.create_index([("reporter_id", ASCENDING)])
db.reports.create_index([("status", ASCENDING)])
db.reports.create_index([("severity", ASCENDING)])
db.reports.create_index([("created_at", DESCENDING)])

# Users (Citizens + Team Heads + Admins)
db.users.create_index([("email", ASCENDING)], unique=True, sparse=True)
db.users.create_index([("google_id", ASCENDING)], sparse=True)
db.users.create_index([("phone", ASCENDING)], sparse=True)
db.users.create_index([("role", ASCENDING)])
db.users.create_index([("team", ASCENDING)], sparse=True)
db.users.create_index([("is_team_head", ASCENDING)], sparse=True)

# Tasks
db.tasks.create_index([("team_head_id", ASCENDING)])
db.tasks.create_index([("assigned_team", ASCENDING)])
db.tasks.create_index([("report_id", ASCENDING)])
db.tasks.create_index([("status", ASCENDING)])

# Workers (daily-wage, no login)
db.workers.create_index([("team", ASCENDING)])
db.workers.create_index([("team_head_id", ASCENDING)])
db.workers.create_index([("status", ASCENDING)])
db.workers.create_index([("aadhaar", ASCENDING)], sparse=True)

# Notifications
db.notifications.create_index([("user_id", ASCENDING)])
db.notifications.create_index([("created_at", DESCENDING)])

print("✅ All indexes created.")
client.close()