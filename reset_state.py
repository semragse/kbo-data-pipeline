import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
c  = MongoClient(os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/"))
db = c["kbo_bronze"]

r = db["state_nbb"].update_many(
    {"status": "done"},
    {"$set": {"status": "pending", "deposits": {}}}
)
print(f"Reset to pending: {r.modified_count}")
c.close()
