"""
SCRAPING eJUSTICE — Publications légales des entreprises hôtelières
====================================================================
Interroge le portail eJustice (Moniteur Belge / tribunal de commerce)
pour récupérer les publications légales (faillites, dissolutions,
modifications de statuts, etc.) des entreprises ciblées.

Sources :
  • Moniteur Belge  → https://www.ejustice.just.fgov.be/cgi_tsv/tsv.pl
  • API eJustice     → https://www.ejustice.just.fgov.be/cgi_tsv/tsv.pl?query=...

Collections MongoDB utilisées :
  • enterprise_finale  (lecture — entreprises sources)
  • state_ejustice     (écriture — StateDB eJustice)

Usage :
  # Étape 1 : initialiser la StateDB eJustice depuis enterprise_finale
  python scraping_ejustice.py --init

  # Étape 2 : scraper les publications
  python scraping_ejustice.py --scrape

  # Tout en une commande
  python scraping_ejustice.py --init --scrape
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME    = os.getenv("MONGO_DB",  "kbo_bronze")

OUTPUT_DIR = Path(os.getenv("EJUSTICE_OUTPUT_DIR", "tmp/ejustice"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Portail eJustice — Moniteur Belge
EJUSTICE_BASE = "https://www.ejustice.just.fgov.be"
SEARCH_URL    = f"{EJUSTICE_BASE}/cgi_tsv/tsv.pl"

RATE_DELAY   = 2.5   # secondes entre requêtes (respecter le site)
RETRY_AFTER  = 30    # secondes d'attente si 429 / 503
MAX_RETRIES  = 4

# Codes NACE hôtellerie (même sélection que scraping_nbb.py)
NACE_HOTELLERIE = {
    "55100", "55201", "55202", "55203", "55204",
    "55209", "55300", "55400", "55900",
}

EXCLUDED_JURIDICAL_FORMS = {
    "110", "114", "116", "117",
    "301", "302", "303",
    "310", "320", "330", "340", "350",
    "400", "411", "412", "413", "414", "415",
    "416", "417", "418", "419", "420",
}

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "fr-BE,fr;q=0.9",
}

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── ÉTAPE 1 : Initialisation StateDB eJustice ─────────────────────────────────

def init_state_db(db) -> None:
    """
    Filtre enterprise_finale sur les critères hôtellerie et initialise
    la collection state_ejustice avec status=pending.
    """
    log.info("=" * 65)
    log.info("  INIT StateDB eJustice — Filtrage hôtellerie")
    log.info("=" * 65)

    col_src   = db["enterprise_finale"]
    col_state = db["state_ejustice"]

    col_state.create_index([("EnterpriseNumber", ASCENDING)], unique=True)
    col_state.create_index([("status",           ASCENDING)])

    query = {
        "Status":           "AC",
        "TypeOfEnterprise": "2",
        "JuridicalForm":    {"$nin": list(EXCLUDED_JURIDICAL_FORMS)},
        "activities": {
            "$elemMatch": {
                "NaceCode":       {"$in": list(NACE_HOTELLERIE)},
                "Classification": "MAIN",
            }
        },
    }

    projection = {
        "EnterpriseNumber": 1,
        "activities":       1,
        "denominations":    1,
        "_id":              0,
    }

    total = col_src.count_documents(query)
    log.info(f"Entreprises hôtelières trouvées : {total:,}")

    inserted = 0
    skipped  = 0

    for doc in col_src.find(query, projection):
        num = doc["EnterpriseNumber"]

        if col_state.find_one({"EnterpriseNumber": num}):
            skipped += 1
            continue

        # Nom principal (dénomination 001, FR ou NL)
        name = None
        for d in sorted(doc.get("denominations", []),
                        key=lambda x: x.get("TypeOfDenomination", "999")):
            if d.get("Denomination"):
                name = d["Denomination"]
                break

        col_state.insert_one({
            "EnterpriseNumber": num,
            "name":             name,
            "nace_codes":       [
                a["NaceCode"]
                for a in doc.get("activities", [])
                if a.get("Classification") == "MAIN"
            ],
            "status":           "pending",
            "publications":     [],
            "scraped_at":       None,
            "error":            None,
            "created_at":       datetime.now(timezone.utc),
        })
        inserted += 1

    log.info(f"  ✓ {inserted:,} nouvelles entreprises ajoutées à state_ejustice")
    log.info(f"  → {skipped:,} déjà présentes (ignorées)")
    log.info(f"  Total state_ejustice : {col_state.count_documents({}):,}")


# ── ÉTAPE 2 : Recherche eJustice par numéro d'entreprise ─────────────────────

def _fetch_publications(session: requests.Session,
                        enterprise_number: str) -> list[dict]:
    """
    Interroge eJustice pour récupérer les publications légales
    liées à un numéro d'entreprise.
    Retourne une liste de publications (dict).
    """
    # Numéro sans points pour l'URL (ex: 0878065378)
    num_clean = enterprise_number.replace(".", "")

    params = {
        "language": "fr",
        "query":    f"num_entreprise={num_clean}",
    }

    retries = 0
    while retries <= MAX_RETRIES:
        try:
            r = session.get(SEARCH_URL, params=params, timeout=20)

            if r.status_code in (429, 503):
                retries += 1
                log.warning(f"  {r.status_code} — attente {RETRY_AFTER}s "
                             f"({retries}/{MAX_RETRIES})")
                time.sleep(RETRY_AFTER)
                continue

            if r.status_code == 404:
                return []

            r.raise_for_status()

            # eJustice renvoie du HTML ou du JSON selon l'Accept header
            # On parse le JSON si disponible
            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                data = r.json()
                return data.get("results", data if isinstance(data, list) else [])

            # Fallback : retourner un enregistrement minimal avec l'URL
            return [{
                "url":    r.url,
                "status": r.status_code,
                "raw":    r.text[:500],
            }]

        except requests.RequestException as e:
            retries += 1
            log.warning(f"  Erreur réseau : {e} — retry {retries}/{MAX_RETRIES}")
            time.sleep(RETRY_AFTER)

    raise RuntimeError(f"Échec après {MAX_RETRIES} tentatives pour {enterprise_number}")


# ── ÉTAPE 2 : Scraping principal ──────────────────────────────────────────────

def scrape(db) -> None:
    """
    Scrape eJustice pour toutes les entreprises en status=pending
    dans state_ejustice.
    """
    log.info("=" * 65)
    log.info("  SCRAPING eJustice — Publications légales hôtellerie")
    log.info("=" * 65)

    col_state = db["state_ejustice"]

    pending = col_state.count_documents({"status": "pending"})
    done    = col_state.count_documents({"status": "done"})
    errors  = col_state.count_documents({"status": "error"})
    log.info(f"  pending : {pending:,}  |  done : {done:,}  |  error : {errors:,}")
    log.info("")

    if pending == 0:
        log.info("Aucune entreprise en attente. Fin.")
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    ok = err = 0

    for entry in col_state.find({"status": "pending"}):
        num  = entry["EnterpriseNumber"]
        name = entry.get("name", "—")

        # Marquer en cours
        col_state.update_one(
            {"EnterpriseNumber": num},
            {"$set": {"status": "in_progress"}},
        )

        log.info(f"  [{ok + err + 1}/{pending}]  {num}  {name}")

        try:
            publications = _fetch_publications(session, num)

            # Sauvegarder en JSON local
            out_file = OUTPUT_DIR / f"{num.replace('.', '')}.json"
            out_file.write_text(
                json.dumps(publications, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            col_state.update_one(
                {"EnterpriseNumber": num},
                {"$set": {
                    "status":       "done",
                    "publications": publications,
                    "scraped_at":   datetime.now(timezone.utc),
                    "error":        None,
                }},
            )
            ok += 1
            log.info(f"    ✓ {len(publications)} publication(s)")

        except Exception as e:
            col_state.update_one(
                {"EnterpriseNumber": num},
                {"$set": {
                    "status":     "error",
                    "error":      str(e),
                    "scraped_at": datetime.now(timezone.utc),
                }},
            )
            err += 1
            log.error(f"    ✗ {e}")

        time.sleep(RATE_DELAY)

    log.info("")
    log.info("=" * 65)
    log.info(f"  ✓ done : {ok:,}  |  ✗ error : {err:,}")
    log.info("=" * 65)


# ── Programme principal ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scraping eJustice — Publications légales entreprises hôtelières"
    )
    parser.add_argument("--init",   action="store_true",
                        help="Initialiser la StateDB eJustice depuis enterprise_finale")
    parser.add_argument("--scrape", action="store_true",
                        help="Scraper les publications eJustice (status=pending)")
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
