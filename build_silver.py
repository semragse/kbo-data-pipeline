"""
BUILD enterprise_silver — Transformations Silver sur enterprise_finale
=======================================================================
Applique les 5 transformations Silver et crée enterprise_silver.
enterprise_finale (Bronze) reste INTACTE.

Transformations :
  1. Normalisation des dates  (DD-MM-YYYY → YYYY-MM-DD)
  2. Déduplication des activités  (même NaceCode + Classification → 1 seul)
  3. Adresse unique  (garder uniquement TypeOfAddress = REGO)
  4. Dénomination principale en tête  (TypeOfDenomination = "001")
  5. Décodage codes → labels FR  (JuridicalForm, Status, NaceCode)

Usage :
  python build_silver.py
"""

import csv
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME    = os.getenv("MONGO_DB", "kbo_bronze")
DATA_DIR   = Path(os.getenv("KBO_DIR", str(Path(__file__).parent)))
BATCH_SIZE = 2_000

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Chargement du dictionnaire de codes (collection code) ─────────────────────

def load_code_lookup(db=None) -> dict:
    """
    Lit code.csv et retourne { (Category, Code): { "FR": label, "NL": label } }
    Chargé en mémoire une seule fois (21k entrées ≈ léger).
    """
    lookup: dict[tuple, dict] = {}
    code_csv = DATA_DIR / "code.csv"
    if not code_csv.exists():
        log.warning(f"  code.csv introuvable dans {DATA_DIR} — labels désactivés")
        return lookup
    with open(code_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            category = (row.get("Category") or "").strip()
            code     = (row.get("Code")     or "").strip()
            lang     = (row.get("Language") or "").strip()
            desc     = (row.get("Description") or "").strip()
            if not category or not code:
                continue
            key = (category, code)
            if key not in lookup:
                lookup[key] = {}
            lookup[key][lang] = desc
    log.info(f"  Code lookup chargé depuis code.csv : {len(lookup):,} entrées")
    return lookup


def decode(lookup: dict, category: str, code, lang: str = "FR") -> str | None:
    if code is None:
        return None
    result = lookup.get((category, str(code).strip()), {})
    return result.get(lang) or result.get("NL") or None


# ── Transformations Silver ─────────────────────────────────────────────────────

def normalize_date(date_str: str | None) -> str | None:
    """DD-MM-YYYY → YYYY-MM-DD. Retourne None si format inconnu."""
    if not date_str or not isinstance(date_str, str):
        return date_str
    parts = date_str.strip().split("-")
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return date_str   # format déjà correct ou inconnu


def deduplicate_activities(activities: list) -> list:
    """
    Supprime les vrais doublons : même NaceCode + même Classification.
    Conserve les activités qui ont des codes différents même si même version.
    """
    seen: set[tuple] = set()
    result = []
    for act in activities:
        key = (act.get("NaceCode"), act.get("Classification"))
        if key not in seen:
            seen.add(key)
            result.append(act)
    return result


def keep_rego_address(addresses: list) -> list:
    """Garde uniquement le siège social (REGO). Fallback sur la 1ère adresse."""
    rego = [a for a in addresses if a.get("TypeOfAddress") == "REGO"]
    return rego if rego else addresses[:1]


def sort_denominations(denominations: list) -> list:
    """Met TypeOfDenomination=001 en tête, puis les autres."""
    official = [d for d in denominations if d.get("TypeOfDenomination") == "001"]
    others   = [d for d in denominations if d.get("TypeOfDenomination") != "001"]
    return official + others


def enrich_activities(activities: list, lookup: dict) -> list:
    """Ajoute NaceLabel FR à chaque activité."""
    enriched = []
    for act in activities:
        version   = act.get("NaceVersion", "")
        nace_code = act.get("NaceCode")
        label = (
            decode(lookup, f"Nace{version}", nace_code)
            or decode(lookup, "Nace2025", nace_code)
            or decode(lookup, "Nace2008", nace_code)
        )
        enriched.append({**act, "NaceLabel": label})
    return enriched


def transform(doc: dict, lookup: dict) -> dict:
    """Applique les 5 transformations Silver sur un document enterprise_finale."""

    # 1. Normalisation des dates
    start_date = normalize_date(doc.get("StartDate"))

    # 2. Activités : dédup + enrichissement labels
    activities = deduplicate_activities(doc.get("activities", []))
    activities = enrich_activities(activities, lookup)

    # 3. Adresse unique (REGO)
    addresses = keep_rego_address(doc.get("addresses", []))

    # 4. Dénominations triées (officielle en premier)
    denominations = sort_denominations(doc.get("denominations", []))

    # 5. Labels décodés pour JuridicalForm et Status
    juridical_form  = doc.get("JuridicalForm")
    status          = doc.get("Status")

    return {
        # Champs de base enterprise
        "EnterpriseNumber":    doc.get("EnterpriseNumber"),
        "Status":              status,
        "StatusLabel":         decode(lookup, "Status", status),
        "JuridicalSituation":  doc.get("JuridicalSituation"),
        "TypeOfEnterprise":    doc.get("TypeOfEnterprise"),
        "JuridicalForm":       juridical_form,
        "JuridicalFormLabel":  decode(lookup, "JuridicalForm", juridical_form),
        "JuridicalFormCAC":    doc.get("JuridicalFormCAC"),
        "StartDate":           start_date,          # normalisé YYYY-MM-DD

        # Enrichissements Silver
        "denominations":       denominations,        # officielle en premier
        "addresses":           addresses,            # REGO uniquement
        "activities":          activities,           # dédupliquées + labels
        "contacts":            doc.get("contacts", []),
        "establishments":      doc.get("establishments", []),
        "branches":            doc.get("branches", []),
    }


# ── Programme principal ────────────────────────────────────────────────────────

def main():
    log.info("=" * 65)
    log.info("  BUILD enterprise_silver — Transformations Silver")
    log.info("=" * 65)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    try:
        client.admin.command("ping")
        log.info("✓ MongoDB accessible")
    except Exception as e:
        log.error(f"✗ MongoDB inaccessible : {e}")
        return

    db = client[DB_NAME]

    # Vérifier que enterprise_finale existe
    if "enterprise_finale" not in db.list_collection_names():
        log.error("✗ La collection enterprise_finale n'existe pas.")
        log.error("  → Lance d'abord : python build_enterprise_finale.py")
        client.close()
        return

    # Charger le dictionnaire de codes
    log.info("Chargement du dictionnaire de codes depuis code.csv...")
    lookup = load_code_lookup()

    # Préparer la collection Silver
    col_src = db["enterprise_finale"]
    col_dst = db["enterprise_silver"]

    if "enterprise_silver" in db.list_collection_names():
        log.info("Collection enterprise_silver existante — suppression...")
        col_dst.drop()

    total = col_src.count_documents({})
    log.info(f"Documents à transformer : {total:,}")
    log.info("")

    start  = time.time()
    done   = 0
    batch  = []

    cursor = col_src.find({}, no_cursor_timeout=True).batch_size(BATCH_SIZE)

    for doc in cursor:
        doc.pop("_id", None)
        batch.append(transform(doc, lookup))

        if len(batch) >= BATCH_SIZE:
            col_dst.insert_many(batch, ordered=False)
            done += len(batch)
            pct  = done / total * 100
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            log.info(f"  {done:>8,} / {total:,}  ({pct:.1f}%)  —  {rate:.0f} doc/s")
            batch = []

    if batch:
        col_dst.insert_many(batch, ordered=False)
        done += len(batch)

    cursor.close()

    # Index
    log.info("Création des index...")
    col_dst.create_index([("EnterpriseNumber", ASCENDING)], unique=True)
    col_dst.create_index([("Status", ASCENDING)])
    col_dst.create_index([("JuridicalForm", ASCENDING)])
    col_dst.create_index([("activities.NaceCode", ASCENDING)])

    elapsed = time.time() - start
    final_count = col_dst.count_documents({})

    log.info("")
    log.info("=" * 65)
    log.info(f"  ✓ enterprise_silver : {final_count:,} documents")
    log.info(f"  Durée : {elapsed:.0f}s")
    log.info("=" * 65)

    client.close()


if __name__ == "__main__":
    main()
