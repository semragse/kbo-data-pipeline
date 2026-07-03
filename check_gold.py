import os, json
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
c  = MongoClient(os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/"))
db = c["kbo_bronze"]

# Stats globales
total     = db["hotel_gold"].count_documents({})
with_data = db["hotel_gold"].count_documents({"$expr": {"$gt": [{"$size": "$years"}, 0]}})
with_name = db["hotel_gold"].count_documents({"name": {"$ne": None}})
print(f"Total: {total} | Avec données: {with_data} | Avec nom: {with_name}")

# Exemple d'un doc avec nom ET données
doc = db["hotel_gold"].find_one({"$expr": {"$gt": [{"$size": "$years"}, 0]}, "name": {"$ne": None}})
if doc:
    doc.pop("_id", None); doc.pop("last_updated", None)
    out = json.dumps(doc, indent=2, ensure_ascii=False, default=str)
    print(out[:2500])

c.close()
