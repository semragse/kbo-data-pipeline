"""
BUILD enterprise_finale — Join Bronze collections → un document complet par entreprise
========================================================================================
Prend les 7 collections Bronze brutes et les joint sur EnterpriseNumber
pour produire enterprise_finale : un seul document MongoDB par entreprise
qui regroupe toutes les informations disponibles.

Structure du document produit :
{
  "EnterpriseNumber": "0878.065.378",
  "Status": "AC",
  "JuridicalForm": "416",
  ...                              ← champs enterprise de base
  "denominations":  [...],         ← tous les noms (FR/NL)
  "addresses":      [...],         ← toutes les adresses
  "activities":     [...],         ← toutes les activités NACE
  "contacts":       [...],         ← téléphones, emails, web
  "establishments": [...],         ← établissements
  "branches":       [...]          ← succursales étrangères
}

Usage :
  python build_enterprise_finale.py
"""

import logging
import os
import time
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME    = os.getenv("MONGO_DB", "kbo_bronze")
BATCH_SIZE = 5_000      # enterprises traitées par batch

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def group_by(cursor, key: str) -> dict:
    """Groupe les documents d'un curseur par valeur de clé."""
    result: dict[str, list] = {}
    for doc in cursor:
        doc.pop("_id", None)
        k = doc.get(key)
        if k not in result:
            result[k] = []
        result[k].append(doc)
    return result


def process_batch(batch: list, col_den, col_adr, col_act, col_con, col_est, col_bra, col_out):
    """Joint une batch d'entreprises avec toutes les collections et insère dans col_out."""
    numbers = [e["EnterpriseNumber"] for e in batch]

    # Lookups groupés — une seule requête par collection par batch
    dens = group_by(col_den.find({"EntityNumber":     {"$in": numbers}}), "EntityNumber")
    adrs = group_by(col_adr.find({"EntityNumber":     {"$in": numbers}}), "EntityNumber")
    acts = group_by(col_act.find({"EntityNumber":     {"$in": numbers}}), "EntityNumber")
    cons = group_by(col_con.find({"EntityNumber":     {"$in": numbers}}), "EntityNumber")
    ests = group_by(col_est.find({"EnterpriseNumber": {"$in": numbers}}), "EnterpriseNumber")
    bras = group_by(col_bra.find({"EnterpriseNumber": {"$in": numbers}}), "EnterpriseNumber")

    docs = []
    for ent in batch:
        num = ent["EnterpriseNumber"]
        ent.pop("_id", None)

        docs.append({
            **ent,
            "denominations":  dens.get(num, []),
            "addresses":      adrs.get(num, []),
            "activities":     acts.get(num, []),
            "contacts":       cons.get(num, []),
            "establishments": ests.get(num, []),
            "branches":       bras.get(num, []),
        })

    if docs:
        col_out.insert_many(docs, ordered=False)


# ── Programme principal ────────────────────────────────────────────────────────

def main():
    log.info("=" * 65)
    log.info("  BUILD enterprise_finale — Join Bronze → 1 doc/entreprise")
    log.info("=" * 65)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    try:
        client.admin.command("ping")
        log.info("✓ MongoDB accessible")
    except Exception as e:
        log.error(f"✗ MongoDB inaccessible : {e}")
        return

    db = client[DB_NAME]

    col_ent = db["enterprise"]
    col_den = db["denomination"]
    col_adr = db["address"]
    col_act = db["activity"]
    col_con = db["contact"]
    col_est = db["establishment"]
    col_bra = db["branch"]
    col_out = db["enterprise_finale"]

    # Suppression de la collection si elle existe déjà
    if "enterprise_finale" in db.list_collection_names():
        log.info("Collection enterprise_finale existante — suppression...")
        col_out.drop()

    total = col_ent.count_documents({})
    log.info(f"Entreprises à traiter : {total:,}")
    log.info("")

    start   = time.time()
    batch   = []
    done    = 0

    cursor = col_ent.find({}, no_cursor_timeout=True).batch_size(BATCH_SIZE)

    for ent in cursor:
        batch.append(ent)

        if len(batch) >= BATCH_SIZE:
            process_batch(batch, col_den, col_adr, col_act, col_con, col_est, col_bra, col_out)
            done += len(batch)
            pct  = done / total * 100
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            log.info(f"  {done:>8,} / {total:,}  ({pct:.1f}%)  —  {rate:.0f} ent/s")
            batch = []

    # Dernier batch
    if batch:
        process_batch(batch, col_den, col_adr, col_act, col_con, col_est, col_bra, col_out)
        done += len(batch)

    cursor.close()

    # Index sur EnterpriseNumber
    log.info("Création de l'index EnterpriseNumber...")
    col_out.create_index([("EnterpriseNumber", ASCENDING)], unique=True)

    elapsed = time.time() - start
    final_count = col_out.count_documents({})

    log.info("")
    log.info("=" * 65)
    log.info(f"  ✓ enterprise_finale : {final_count:,} documents")
    log.info(f"  Durée : {elapsed:.0f}s")
    log.info("=" * 65)

    client.close()


if __name__ == "__main__":
    main()
