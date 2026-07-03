"""
BUILD hotel_gold — Couche Gold : ratios financiers hôtellerie
=============================================================
Lit les CSV PCMN téléchargés par scraping_nbb.py, calcule les ratios
financiers par exercice et consolide le tout dans la collection hotel_gold.

Un document par entreprise, tous les exercices dans un tableau.

Usage :
  python build_gold.py
  python build_gold.py --limit 500
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, UpdateOne, MongoClient

load_dotenv()

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME    = os.getenv("MONGO_DB",  "kbo_bronze")
HDFS_DIR   = Path(os.getenv("NBB_OUTPUT_DIR", "tmp/hdfs"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ── Parsing CSV PCMN (identique au projet de référence) ───────────────────────

def _to_float(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def parse_csv(content: bytes) -> tuple[dict[str, float], dict[str, str]]:
    """Parse un CSV NBB → (postes {code_pcmn: montant}, métadonnées)."""
    text   = content.decode("utf-8-sig", errors="replace")
    values: dict[str, float] = {}
    meta:   dict[str, str]   = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        key, raw = row[0].strip(), row[1]
        if not key:
            continue
        if key[0].isdigit():
            amount = _to_float(raw)
            if amount is not None:
                values[key] = amount
        else:
            meta[key] = raw.strip()
    return values, meta


def _first(values: dict[str, float], *codes: str) -> float | None:
    for code in codes:
        if code in values:
            return values[code]
    return None


def _sum(values: dict[str, float], codes: list[str]) -> float | None:
    present = [values[c] for c in codes if c in values]
    return sum(present) if present else None


def extract_accounts(values: dict[str, float]) -> dict:
    tresorerie = _first(values, "54/58")
    if tresorerie is None:
        tresorerie = _sum(values, ["54", "55"])
    fonds_propres = _first(values, "10/15")
    if fonds_propres is None:
        fonds_propres = _sum(values, ["10", "11", "12", "13", "14", "15"])
    dettes = _sum(values, ["17", "43"])
    if dettes is None:
        dettes = _first(values, "17/49")  # abrégé
    return {
        # CA : schéma complet=70, abrégé=70/76A
        "chiffre_affaires":   _first(values, "70", "70/76A"),
        # Achats : schéma complet=60, abrégé=60/61 ou 60/66A
        "achats":             _first(values, "60", "60/61", "60/66A"),
        "variation_stocks":   _first(values, "71"),
        "ebit":               _first(values, "9901"),
        "resultat_net":       _first(values, "9904"),
        "tresorerie":         tresorerie,
        "dettes_financieres": dettes,
        "fonds_propres":      fonds_propres,
        "capital_souscrit":   _first(values, "100", "10"),
    }


def _div(num, den, factor=1.0):
    if num is None or not den:
        return None
    return round(num / den * factor, 2)


def compute_ratios(acc: dict) -> dict:
    ca = acc.get("chiffre_affaires")
    return {
        "marge_brute":          marge_brute(acc, {}),
        "marge_nette_pct":      _div(acc["resultat_net"],       ca,                       100),
        "roe_pct":              _div(acc["resultat_net"],       acc["fonds_propres"],     100),
        "liquidite":            _div(acc["tresorerie"],         acc["dettes_financieres"]),
        "taux_endettement_pct": _div(acc["dettes_financieres"], acc["fonds_propres"],     100),
    }


def marge_brute(acc: dict, values: dict) -> float | None:
    ca = acc.get("chiffre_affaires")
    if ca is not None:
        return round(ca - (acc["achats"] or 0) + (acc["variation_stocks"] or 0), 2)
    return _first(values, "9900")   # repli schéma abrégé/micro


def schema_type(values: dict) -> str:
    n = len(values)
    if n >= 150:
        return "full"
    if n >= 90:
        return "abrege"
    return "micro"


def build_year(content: bytes, fallback_year: int | None = None) -> dict | None:
    values, meta = parse_csv(content)
    if not values:
        return None
    end_date = meta.get("Accounting period end date", "")
    year = int(end_date[:4]) if end_date[:4].isdigit() else fallback_year
    if year is None:
        return None
    acc = extract_accounts(values)
    return {
        "year":          year,
        **acc,
        "ratios":        compute_ratios(acc),
        "model_code":    meta.get("Model code"),
        "reference":     meta.get("Reference number"),
        "schema_type":   schema_type(values),
    }


# ── Construction Gold ─────────────────────────────────────────────────────────

def build_document(state_doc: dict, silver_col) -> dict | None:
    """Consolidation d'un document Gold depuis les CSV PCMN de la StateDB."""
    num  = state_doc.get("EnterpriseNumber") or state_doc.get("_id")
    name = state_doc.get("name")

    # Chercher le nom dans enterprise_silver si absent
    if not name and silver_col is not None:
        silver_doc = silver_col.find_one(
            {"EnterpriseNumber": num},
            {"denominations": 1}
        )
        if silver_doc:
            for d in silver_doc.get("denominations", []):
                if d.get("Denomination"):
                    name = d["Denomination"]
                    break

    deposits = state_doc.get("deposits") or {}
    by_year: dict[int, dict] = {}

    # Itérer sur les dépôts enregistrés
    for year_str, dep in deposits.items():
        csv_path = dep.get("csv_path")
        year_hint = dep.get("year")
        if not csv_path:
            continue
        try:
            content = Path(csv_path).read_bytes()
        except OSError:
            continue
        yr_doc = build_year(content, fallback_year=year_hint)
        if yr_doc is None:
            continue
        yr = yr_doc["year"]
        kept = by_year.get(yr)
        if kept is None or str(yr_doc.get("reference") or "") >= str(kept.get("reference") or ""):
            by_year[yr] = yr_doc

    # Fallback : chercher les CSV dans tmp/hdfs/{num}/nbb/
    if not by_year:
        bce_clean = num.replace(".", "")
        base = HDFS_DIR / bce_clean / "nbb"
        if base.exists():
            for csv_file in sorted(base.rglob("*.csv")):
                parts = csv_file.parts
                try:
                    yr_hint = int(csv_file.parent.name)
                except ValueError:
                    yr_hint = None
                try:
                    content = csv_file.read_bytes()
                    yr_doc  = build_year(content, fallback_year=yr_hint)
                    if yr_doc:
                        yr = yr_doc["year"]
                        by_year[yr] = yr_doc
                except OSError:
                    pass

    if not by_year:
        return None

    years = [by_year[y] for y in sorted(by_year)]
    return {
        "enterprise_number": num,
        "name":              name,
        "nace_codes":        state_doc.get("nace_codes", []),
        "schema_type":       years[-1]["schema_type"],
        "last_updated":      datetime.now(timezone.utc),
        "years":             years,
    }


# ── Programme principal ────────────────────────────────────────────────────────

def main(limit: int | None = None) -> None:
    log.info("=" * 65)
    log.info("  BUILD hotel_gold — Ratios financiers hôtellerie")
    log.info("=" * 65)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    try:
        client.admin.command("ping")
        log.info("✓ MongoDB accessible")
    except Exception as e:
        log.error(f"✗ MongoDB inaccessible : {e}")
        return

    db        = client[DB_NAME]
    col_state  = db["state_nbb"]
    col_silver = db["enterprise_silver"]
    col_gold   = db["hotel_gold"]

    col_gold.create_index([("enterprise_number", ASCENDING)], unique=True)
    col_gold.create_index([("nace_codes",         ASCENDING)])

    query  = {"status": {"$in": ["done", "pending"]}}
    cursor = col_state.find(query).limit(limit or 0)
    total  = col_state.count_documents(query)
    if limit:
        total = min(total, limit)

    log.info(f"Entreprises à traiter : {total:,}")
    log.info("")

    start      = time.time()
    ops        = []
    processed  = 0
    with_data  = 0
    year_count = 0

    for state_doc in cursor:
        processed += 1
        doc = build_document(state_doc, col_silver)
        num = state_doc.get("EnterpriseNumber") or state_doc.get("_id")

        # Si pas de CSV, insérer quand même avec years=[]
        if doc is None:
            doc = {
                "enterprise_number": num,
                "name":              state_doc.get("name"),
                "nace_codes":        state_doc.get("nace_codes", []),
                "schema_type":       "unknown",
                "last_updated":      datetime.now(timezone.utc),
                "years":             [],
            }
        else:
            with_data  += 1
            year_count += len(doc["years"])
        ops.append(UpdateOne(
            {"enterprise_number": doc["enterprise_number"]},
            {"$set": doc},
            upsert=True,
        ))
        if len(ops) >= 500:
            col_gold.bulk_write(ops, ordered=False)
            ops.clear()
            log.info(f"  {processed:>6,} / {total:,}  — {with_data} avec données")

    if ops:
        col_gold.bulk_write(ops, ordered=False)

    elapsed = time.time() - start
    log.info("")
    log.info("=" * 65)
    log.info(f"  ✓ hotel_gold      : {col_gold.count_documents({}):,} documents")
    log.info(f"  Avec exercices    : {with_data:,}  ({year_count:,} exercices)")
    log.info(f"  Durée             : {elapsed:.0f}s")
    log.info("=" * 65)
    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    main(limit=args.limit)
