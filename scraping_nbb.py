"""
SCRAPING NBB — Ciblage hôtellerie + dépôts financiers 2021-2025
================================================================
1. Filtre enterprise_finale sur les codes NACE hôtellerie
2. Charge les entreprises cibles dans la StateDB (collection state_nbb)
3. Scrape l'API NBB CBSO pour récupérer les dépôts financiers 2021-2025
4. Gère le rate-limit 429 avec backoff automatique
5. Met à jour la StateDB (pending → in_progress → done / error)

Usage :
  # Étape 1 : remplir la StateDB
  python scraping_nbb.py --init

  # Étape 2 : lancer le scraping
  python scraping_nbb.py --scrape

  # Les deux d'un coup
  python scraping_nbb.py --init --scrape
"""

import argparse
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────

MONGO_URI   = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME     = os.getenv("MONGO_DB", "kbo_bronze")
NBB_API_KEY = os.getenv("NBB_API_KEY", "")

OUTPUT_DIR  = Path(os.getenv("NBB_OUTPUT_DIR", "tmp/hdfs"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NBB_BASE    = "https://consult.cbso.nbb.be/api"
YEAR_MIN    = 2021

RATE_LIMIT_DELAY  = 2.0     # secondes entre chaque requête (safe)
RETRY_AFTER_429   = 60      # secondes d'attente si 429
MAX_RETRIES_429   = 5

# Codes NACE hôtellerie retenus par le prof
NACE_HOTELLERIE = {
    "55100",   # Hôtels et hébergement similaire
    "55201",   # Auberges de jeunesse
    "55202",   # Centres et villages de vacances
    "55203",   # Gîtes de vacances, appartements meublés
    "55204",   # Chambres d'hôtes
    "55209",   # Autres hébergements courte durée
    "55300",   # Terrains de camping et parcs caravanes
    "55400",   # Intermédiaires hébergement (Airbnb/Booking)
    "55900",   # Autres hébergements
}

# Formes juridiques exclues (publiques, communes, etc.)
EXCLUDED_JURIDICAL_FORMS = {
    # Entités publiques
    "110", "114", "116", "117",
    # Services fédéraux
    "301", "302", "303",
    # Autorités régionales
    "310", "320", "330", "340", "350",
    # Communes, CPAS, intercommunales
    "400", "411", "412", "413", "414", "415",
    "416", "417", "418", "419", "420",
}

HEADERS = {
    "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":         "application/json, text/plain, */*",
    "Accept-Language": "fr-BE,fr;q=0.9",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── ÉTAPE 1 : Filtrage + initialisation StateDB ───────────────────────────────

def init_state_db(db):
    """
    Filtre enterprise_finale sur les critères hôtellerie et charge
    les entreprises cibles dans la collection state_nbb.
    """
    log.info("=" * 65)
    log.info("  INIT StateDB — Filtrage hôtellerie")
    log.info("=" * 65)

    col_src   = db["enterprise_finale"]
    col_state = db["state_nbb"]

    # Créer les index StateDB si nécessaire
    col_state.create_index([("EnterpriseNumber", ASCENDING)], unique=True)
    col_state.create_index([("status", ASCENDING)])

    # Filtre MongoDB sur enterprise_finale
    query = {
        "Status":             "AC",           # actives uniquement
        "TypeOfEnterprise":   "2",            # personnes morales privées
        "JuridicalForm":      {"$nin": list(EXCLUDED_JURIDICAL_FORMS)},
        "activities": {
            "$elemMatch": {
                "NaceCode":       {"$in": list(NACE_HOTELLERIE)},
                "Classification": "MAIN",
            }
        },
    }

    projection = {"EnterpriseNumber": 1, "activities": 1, "_id": 0}

    total_source = col_src.count_documents(query)
    log.info(f"Entreprises hôtelières trouvées : {total_source:,}")

    inserted = 0
    skipped  = 0

    for doc in col_src.find(query, projection):
        num = doc["EnterpriseNumber"]

        # Ne pas écraser un enregistrement déjà existant (reprise possible)
        if col_state.find_one({"EnterpriseNumber": num}):
            skipped += 1
            continue

        # Codes NACE principaux de cette entreprise
        nace_codes = [
            a["NaceCode"] for a in doc.get("activities", [])
            if a.get("Classification") == "MAIN"
        ]

        col_state.insert_one({
            "EnterpriseNumber": num,
            "nace_codes":       nace_codes,
            "status":           "pending",
            "filings_count":    0,
            "scraped_at":       None,
            "error":            None,
            "created_at":       datetime.utcnow(),
        })
        inserted += 1

    log.info(f"  ✓ {inserted:,} nouvelles entreprises ajoutées à state_nbb")
    log.info(f"  → {skipped:,} déjà présentes (ignorées)")
    log.info(f"  Total state_nbb : {col_state.count_documents({}):,}")


# ── ÉTAPE 2 : Session NBB ─────────────────────────────────────────────────────

def make_nbb_session(enterprise_number: str) -> requests.Session:
    """Crée une session avec les cookies NBB nécessaires."""
    session = requests.Session()
    session.headers.update(HEADERS)
    page_url = f"https://consult.cbso.nbb.be/consult-enterprise/{enterprise_number}"
    session.headers["Referer"] = page_url
    try:
        session.get(page_url, timeout=15)
    except Exception:
        pass
    return session


def get_deposits(session: requests.Session, enterprise_number: str) -> list:
    """Récupère tous les dépôts disponibles >= YEAR_MIN."""
    url = (
        f"{NBB_BASE}/rs-consult/published-deposits"
        f"?page=0&size=50&enterpriseNumber={enterprise_number}"
        f"&sort=periodEndDate,desc"
    )
    retries = 0
    while retries <= MAX_RETRIES_429:
        r = session.get(url, timeout=15)
        if r.status_code == 429:
            retries += 1
            log.warning(f"  429 Rate Limit — attente {RETRY_AFTER_429}s ({retries}/{MAX_RETRIES_429})")
            time.sleep(RETRY_AFTER_429)
            continue
        r.raise_for_status()
        data = r.json()
        deposits = data.get("content", [])
        # Filtrer >= YEAR_MIN
        return [d for d in deposits if d.get("periodEndDateYear", 0) >= YEAR_MIN]
    raise RuntimeError(f"429 persistant pour {enterprise_number}")


def download_csv(session: requests.Session, deposit_id: str,
                 enterprise_number: str, year: int, reference: str) -> Path | None:
    """Télécharge le CSV comptable PCMN d'un dépôt et le sauvegarde localement."""
    safe_ref = reference.replace("/", "_")
    dest = OUTPUT_DIR / enterprise_number / "nbb" / str(year) / f"{safe_ref}.csv"
    if dest.exists():
        return dest

    url = f"{NBB_BASE}/external/broker/public/deposits/consult/csv/{deposit_id}"
    retries = 0
    while retries <= MAX_RETRIES_429:
        r = session.get(url, timeout=30)
        if r.status_code == 429:
            retries += 1
            log.warning(f"  429 CSV — attente {RETRY_AFTER_429}s")
            time.sleep(RETRY_AFTER_429)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        log.info(f"    ✓ {dest.name}  ({len(r.content)} bytes)")
        return dest

    return None


# ── ÉTAPE 2 : Scraping principal ──────────────────────────────────────────────

def scrape(db):
    """Scrape les dépôts NBB pour toutes les entreprises pending dans state_nbb."""
    log.info("=" * 65)
    log.info("  SCRAPING NBB — Dépôts financiers 2021-2025")
    log.info("=" * 65)

    col_state = db["state_nbb"]

    pending = col_state.count_documents({"status": "pending"})
    done    = col_state.count_documents({"status": "done"})
    log.info(f"  pending : {pending:,}  |  done : {done:,}")
    log.info("")

    if pending == 0:
        log.info("Aucune entreprise en attente. Fin.")
        return

    # Ouvrir une session de référence pour les cookies
    session = make_nbb_session("0203430576")

    for state_doc in col_state.find({"status": "pending"}, no_cursor_timeout=False):
        num = state_doc["EnterpriseNumber"]
        bce = num.replace(".", "")     # format sans points pour l'API NBB

        # Marquer in_progress
        col_state.update_one(
            {"EnterpriseNumber": num},
            {"$set": {"status": "in_progress", "started_at": datetime.utcnow()}},
        )

        try:
            log.info(f"▶  {num}")
            deposits = get_deposits(session, bce)
            log.info(f"  {len(deposits)} dépôts >= {YEAR_MIN}")

            ok = 0
            deposits_meta = {}   # { year: { metadata } }
            for dep in deposits:
                year      = dep.get("periodEndDateYear")
                dep_id    = dep.get("id", "")
                reference = str(dep.get("reference") or dep_id)
                csv_path  = download_csv(session, dep_id, bce, year or 0, reference)
                if csv_path:
                    ok += 1
                # Sauvegarder les métadonnées de chaque exercice
                if year:
                    deposits_meta[str(year)] = {
                        "id":           dep_id,
                        "reference":    reference,
                        "year":         year,
                        "csv_path":     str(csv_path) if csv_path else None,
                        "period_start": dep.get("periodStartDate"),
                        "period_end":   dep.get("periodEndDate"),
                    }
                time.sleep(RATE_LIMIT_DELAY)

            # Marquer done + sauvegarder les métadonnées des dépôts
            col_state.update_one(
                {"EnterpriseNumber": num},
                {"$set": {
                    "status":        "done",
                    "filings_count": ok,
                    "deposits":      deposits_meta,
                    "scraped_at":    datetime.utcnow(),
                    "error":         None,
                }},
            )
            log.info(f"  ✓ {ok}/{len(deposits)} PDFs téléchargés")

        except Exception as e:
            log.error(f"  ✗ Erreur pour {num} : {e}")
            col_state.update_one(
                {"EnterpriseNumber": num},
                {"$set": {
                    "status": "error",
                    "error":  str(e),
                }},
            )

        time.sleep(RATE_LIMIT_DELAY)

    # Résumé final
    log.info("")
    log.info("=" * 65)
    for status in ("pending", "in_progress", "done", "error"):
        count = col_state.count_documents({"status": status})
        log.info(f"  {status:<12} : {count:,}")
    log.info("=" * 65)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scraping NBB hôtellerie")
    parser.add_argument("--init",   action="store_true", help="Initialiser la StateDB")
    parser.add_argument("--scrape", action="store_true", help="Lancer le scraping")
    args = parser.parse_args()

    if not args.init and not args.scrape:
        parser.print_help()
        return

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    try:
        client.admin.command("ping")
        log.info("✓ MongoDB accessible")
    except Exception as e:
        log.error(f"✗ MongoDB inaccessible : {e}")
        return

    db = client[DB_NAME]

    if args.init:
        init_state_db(db)

    if args.scrape:
        scrape(db)

    client.close()


if __name__ == "__main__":
    main()
