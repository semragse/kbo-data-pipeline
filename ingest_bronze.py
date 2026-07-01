"""
INGESTION BRONZE — KBO Open Data → MongoDB
==========================================
Charge les 9 fichiers CSV KBO dans la base MongoDB `kbo_bronze`.
Chaque fichier CSV devient une collection distincte (couche Bronze = données brutes).

Collections créées :
  - enterprise      (EnterpriseNumber, Status, JuridicalForm, ...)
  - denomination    (EntityNumber, Language, TypeOfDenomination, Denomination)
  - address         (EntityNumber, TypeOfAddress, Zipcode, Municipality, ...)
  - activity        (EntityNumber, ActivityGroup, NaceVersion, NaceCode, ...)
  - contact         (EntityNumber, ContactType, Value)
  - establishment   (EstablishmentNumber, StartDate, EnterpriseNumber)
  - branch          (Id, StartDate, EnterpriseNumber)
  - code            (Category, Code, Language, Description)
  - meta            (Variable, Value)

Usage :
  python ingest_bronze.py
"""

import logging
import os
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from pymongo.errors import BulkWriteError

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────

MONGO_URI   = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME     = os.getenv("MONGO_DB", "kbo_bronze")
DATA_DIR    = Path(os.getenv("KBO_DIR", str(Path(__file__).parent)))
CHUNK_SIZE  = 50_000                         # lignes lues par batch (RAM safe)

# Mapping fichier CSV → nom de collection MongoDB
CSV_COLLECTIONS = {
    "meta.csv":           "meta",
    "code.csv":           "code",
    "enterprise.csv":     "enterprise",
    "denomination.csv":   "denomination",
    "address.csv":        "address",
    "contact.csv":        "contact",
    "establishment.csv":  "establishment",
    "branch.csv":         "branch",
    "activity.csv":       "activity",   # 1.5 GB — traité en chunks
}

# Index utiles pour les requêtes futures (couche Silver/Gold)
INDEXES = {
    "enterprise":    [("EnterpriseNumber", ASCENDING)],
    "denomination":  [("EntityNumber", ASCENDING)],
    "address":       [("EntityNumber", ASCENDING)],
    "activity":      [("EntityNumber", ASCENDING)],
    "contact":       [("EntityNumber", ASCENDING)],
    "establishment": [("EnterpriseNumber", ASCENDING)],
    "branch":        [("EnterpriseNumber", ASCENDING)],
    "code":          [("Category", ASCENDING), ("Code", ASCENDING)],
}

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def clean_record(record: dict) -> dict:
    """Supprime les valeurs NaN/None pour alléger les documents MongoDB."""
    return {k: v for k, v in record.items() if pd.notna(v) and v != ""}


def ingest_csv(collection, csv_path: Path) -> int:
    """
    Lit un CSV en chunks et insère les documents dans la collection MongoDB.
    Retourne le nombre total de documents insérés.
    """
    total_inserted = 0
    chunk_num = 0

    for chunk in pd.read_csv(
        csv_path,
        dtype=str,
        keep_default_na=False,
        chunksize=CHUNK_SIZE,
        encoding="utf-8",
    ):
        chunk_num += 1
        records = [clean_record(r) for r in chunk.to_dict("records")]

        try:
            result = collection.insert_many(records, ordered=False)
            total_inserted += len(result.inserted_ids)
        except BulkWriteError as e:
            # Certains docs peuvent être dupliqués si on re-exécute le script
            inserted = e.details.get("nInserted", 0)
            total_inserted += inserted
            log.warning(f"    BulkWriteError chunk {chunk_num}: {inserted} insérés, erreurs ignorées")

        log.info(f"    chunk {chunk_num:>4} — {total_inserted:>10,} docs insérés")

    return total_inserted


# ── Programme principal ────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("  INGESTION BRONZE — KBO Open Data → MongoDB")
    log.info("=" * 60)

    # Connexion MongoDB
    log.info(f"Connexion à {MONGO_URI} ...")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    try:
        client.admin.command("ping")
        log.info("  ✓ MongoDB accessible")
    except Exception as e:
        log.error(f"  ✗ Impossible de joindre MongoDB : {e}")
        log.error("  → Vérifiez que le conteneur est bien démarré : podman-compose up -d")
        return

    db = client[DB_NAME]
    log.info(f"Base de données : {DB_NAME}")
    log.info("")

    grand_total = 0
    start_all = time.time()

    for filename, col_name in CSV_COLLECTIONS.items():
        csv_path = DATA_DIR / filename
        if not csv_path.exists():
            log.warning(f"  Fichier introuvable, ignoré : {filename}")
            continue

        size_mb = csv_path.stat().st_size / (1024 ** 2)
        log.info(f"▶  {filename}  ({size_mb:.1f} MB) → collection '{col_name}'")

        collection = db[col_name]

        # Vider la collection si elle existe déjà (idempotent)
        existing = collection.count_documents({})
        if existing > 0:
            log.info(f"    Collection existante ({existing:,} docs) — suppression et réingestion...")
            collection.drop()
            collection = db[col_name]

        t0 = time.time()
        count = ingest_csv(collection, csv_path)
        elapsed = time.time() - t0

        # Créer les index après l'ingestion (plus rapide)
        if col_name in INDEXES:
            for index_field in INDEXES[col_name]:
                collection.create_index([index_field])
            log.info(f"    Index créés : {[f[0] for f in INDEXES[col_name]]}")

        grand_total += count
        log.info(f"    ✓ {count:,} documents  |  {elapsed:.1f}s")
        log.info("")

    elapsed_all = time.time() - start_all
    log.info("=" * 60)
    log.info(f"  TERMINÉ — {grand_total:,} documents au total")
    log.info(f"  Durée totale : {elapsed_all:.0f}s")
    log.info(f"  Base         : {DB_NAME}")
    log.info(f"  Collections  : {list(db.list_collection_names())}")
    log.info("=" * 60)

    client.close()


if __name__ == "__main__":
    main()
