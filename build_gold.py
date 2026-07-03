"""
BUILD hotel_gold — Couche Gold : ratios financiers hôtellerie
=============================================================
Lit les dépôts financiers NBB (JSON/CSV PCMN) téléchargés par
scraping_nbb.py, calcule les ratios financiers par exercice et
consolide le tout dans la collection hotel_gold.

Un document par entreprise, tous les exercices dans un tableau :
{
  "enterprise_number": "0878.065.378",
  "name":              "HOTEL EXAMPLE SA",
  "nace_codes":        ["55100"],
  "schema_type":       "full",
  "last_updated":      "2025-01-15T10:30:00",
  "years": [
    {
      "year":               2023,
      "chiffre_affaires":   500000.0,
      "achats":             200000.0,
      "variation_stocks":   10000.0,
      "ebit":               80000.0,
      "resultat_net":       60000.0,
      "tresorerie":         150000.0,
      "dettes_financieres": 300000.0,
      "fonds_propres":      400000.0,
      "capital_souscrit":   100000.0,
      "ratios": {
        "marge_brute":        310000.0,
        "marge_nette_pct":    12.0,
        "roe_pct":            15.0,
        "liquidite":          0.5,
        "taux_endettement_pct": 75.0
      }
    },
    ...
  ]
}

Mapping codes PCMN → champs :
  70         → chiffre_affaires
  60         → achats
  71         → variation_stocks
  9901       → ebit
  9904       → resultat_net
  54 + 55    → tresorerie
  17 + 43    → dettes_financieres
  10..15     → fonds_propres
  100        → capital_souscrit

Usage :
  python build_gold.py
  python build_gold.py --limit 500   # test sur 500 entreprises
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

MONGO_URI   = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME     = os.getenv("MONGO_DB",  "kbo_bronze")
OUTPUT_DIR  = Path(os.getenv("NBB_OUTPUT_DIR", "tmp/nbb_deposits"))
BATCH_SIZE  = 200

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Mapping PCMN ──────────────────────────────────────────────────────────────

# Codes PCMN simples (une seule valeur)
PCMN_SIMPLE = {
    "70":   "chiffre_affaires",
    "60":   "achats",
    "71":   "variation_stocks",
    "9901": "ebit",
    "9904": "resultat_net",
    "100":  "capital_souscrit",
}

# Codes PCMN composés (somme de plusieurs codes)
PCMN_SUM = {
    "tresorerie":         {"54", "55"},
    "dettes_financieres": {"17", "43"},
    "fonds_propres":      {"10", "11", "12", "13", "14", "15"},
}


# ── Parsing PCMN ──────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    """Convertit une valeur en float, retourne 0.0 si invalide."""
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return 0.0


def parse_pcmn(rows: list[dict]) -> dict:
    """
    rows : liste de dicts { "code_pcmn": "...", "valeur": "..." }
    Retourne un dict avec les champs financiers extraits.
    """
    raw: dict[str, float] = {}
    for row in rows:
        code = str(row.get("code_pcmn") or row.get("Code") or "").strip()
        val  = _safe_float(row.get("valeur") or row.get("Valeur") or row.get("Value") or 0)
        raw[code] = val

    result: dict[str, float] = {}

    # Champs simples
    for pcmn_code, field_name in PCMN_SIMPLE.items():
        result[field_name] = raw.get(pcmn_code, 0.0)

    # Champs composés (somme)
    for field_name, pcmn_set in PCMN_SUM.items():
        result[field_name] = sum(raw.get(c, 0.0) for c in pcmn_set)

    return result


def calc_ratios(f: dict) -> dict:
    """Calcule les 5 ratios financiers depuis les champs extraits."""
    ca       = f.get("chiffre_affaires",   0.0)
    achats   = f.get("achats",             0.0)
    var_stk  = f.get("variation_stocks",   0.0)
    res_net  = f.get("resultat_net",       0.0)
    treso    = f.get("tresorerie",         0.0)
    dettes   = f.get("dettes_financieres", 0.0)
    fonds_p  = f.get("fonds_propres",      0.0)

    def _pct(num, den) -> float | None:
        if den and den != 0:
            return round(num / den * 100, 2)
        return None

    def _ratio(num, den) -> float | None:
        if den and den != 0:
            return round(num / den, 4)
        return None

    return {
        "marge_brute":          round(ca - achats + var_stk, 2),
        "marge_nette_pct":      _pct(res_net, ca),
        "roe_pct":              _pct(res_net, fonds_p),
        "liquidite":            _ratio(treso, dettes),
        "taux_endettement_pct": _pct(dettes, fonds_p),
    }


# ── Lecture des dépôts locaux ─────────────────────────────────────────────────

def load_filings_from_state(state_doc: dict) -> list[dict]:
    """
    Extrait les données PCMN depuis le champ 'deposits' de la StateDB.
    scraping_nbb.py stocke les dépôts dans state_nbb.deposits sous forme :
      { "2021": [...rows...], "2022": [...rows...], ... }
    """
    deposits = state_doc.get("deposits") or {}

    # Si deposits est une liste (format alternatif)
    if isinstance(deposits, list):
        # Essayer de regrouper par année
        by_year: dict[int, list] = {}
        for item in deposits:
            year = item.get("periodEndDateYear") or item.get("year")
            if year:
                by_year.setdefault(int(year), []).append(item)
        deposits = {str(k): v for k, v in by_year.items()}

    filings = []
    for year_str, rows in deposits.items():
        try:
            year = int(year_str)
        except (ValueError, TypeError):
            continue

        if not isinstance(rows, list):
            rows = [rows] if rows else []

        fields = parse_pcmn(rows)
        ratios = calc_ratios(fields)

        filings.append({
            "year":              year,
            **fields,
            "ratios":            ratios,
        })

    return sorted(filings, key=lambda x: x["year"])


def load_filings_from_json(enterprise_number: str) -> list[dict]:
    """
    Fallback : lit le fichier JSON local téléchargé par scraping_nbb.py
    tmp/nbb_deposits/{num_clean}.json
    """
    num_clean = enterprise_number.replace(".", "")
    json_file = OUTPUT_DIR / f"{num_clean}.json"
    if not json_file.exists():
        return []

    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    # Le JSON peut contenir directement des lignes PCMN ou des métadonnées
    if isinstance(data, list) and data:
        first = data[0]
        if "code_pcmn" in first or "Code" in first:
            # C'est directement des lignes PCMN sans année
            fields = parse_pcmn(data)
            ratios = calc_ratios(fields)
            return [{"year": 0, **fields, "ratios": ratios}]

        # Format { year: rows }
        if "year" in first or "periodEndDateYear" in first:
            by_year: dict[int, list] = {}
            for item in data:
                year = item.get("year") or item.get("periodEndDateYear")
                if year:
                    by_year.setdefault(int(year), []).append(item)
            filings = []
            for year, rows in sorted(by_year.items()):
                fields = parse_pcmn(rows)
                ratios = calc_ratios(fields)
                filings.append({"year": year, **fields, "ratios": ratios})
            return filings

    if isinstance(data, dict):
        filings = []
        for year_str, rows in data.items():
            try:
                year = int(year_str)
            except ValueError:
                continue
            if not isinstance(rows, list):
                rows = [rows]
            fields = parse_pcmn(rows)
            ratios = calc_ratios(fields)
            filings.append({"year": year, **fields, "ratios": ratios})
        return sorted(filings, key=lambda x: x["year"])

    return []


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

    db         = client[DB_NAME]
    col_state  = db["state_nbb"]
    col_silver = db["enterprise_silver"]
    col_gold   = db["hotel_gold"]

    # Préparer la collection Gold
    log.info("Suppression de hotel_gold existante (rebuild)...")
    col_gold.drop()

    # Index
    col_gold.create_index([("enterprise_number", ASCENDING)], unique=True)
    col_gold.create_index([("nace_codes",         ASCENDING)])
    col_gold.create_index([("years.year",         ASCENDING)])

    # Traiter les entreprises avec status=done dans state_nbb
    # OU toutes les entreprises hôtelières si pas encore scrapées
    query = {"status": {"$in": ["done", "pending"]}}
    total = col_state.count_documents(query)
    if limit:
        total = min(total, limit)

    log.info(f"Entreprises à traiter : {total:,}")
    log.info("")

    start   = time.time()
    done    = 0
    skipped = 0
    batch   = []

    for entry in col_state.find(query).limit(limit or 0):
        num  = entry["EnterpriseNumber"]
        name = entry.get("name")

        # Récupérer les infos Silver (nom, NACE)
        silver_doc = col_silver.find_one(
            {"EnterpriseNumber": num},
            {"denominations": 1, "activities": 1, "JuridicalFormLabel": 1}
        )

        # Nom depuis Silver si pas dans StateDB
        if not name and silver_doc:
            for d in silver_doc.get("denominations", []):
                if d.get("Denomination"):
                    name = d["Denomination"]
                    break

        # Codes NACE
        nace_codes = entry.get("nace_codes") or []
        if not nace_codes and silver_doc:
            nace_codes = [
                a["NaceCode"]
                for a in silver_doc.get("activities", [])
                if a.get("Classification") == "MAIN"
            ]

        # Exercices financiers : StateDB d'abord, puis JSON local
        filings = load_filings_from_state(entry)
        if not filings:
            filings = load_filings_from_json(num)

        # Déterminer le schema_type selon les champs présents
        schema_type = "unknown"
        if filings:
            f = filings[-1]  # exercice le plus récent
            if f.get("chiffre_affaires", 0) != 0:
                schema_type = "full"
            elif f.get("resultat_net", 0) != 0:
                schema_type = "abrege"
            else:
                schema_type = "micro"

        if not filings:
            skipped += 1

        batch.append({
            "enterprise_number": num,
            "name":              name,
            "nace_codes":        nace_codes,
            "schema_type":       schema_type,
            "last_updated":      datetime.now(timezone.utc),
            "years":             filings,
        })

        if len(batch) >= BATCH_SIZE:
            col_gold.insert_many(batch, ordered=False)
            done += len(batch)
            batch.clear()
            pct = done / total * 100
            log.info(f"  {done:>6,} / {total:,}  ({pct:.1f}%)")

    if batch:
        col_gold.insert_many(batch, ordered=False)
        done += len(batch)

    elapsed = time.time() - start
    final   = col_gold.count_documents({})
    with_years = col_gold.count_documents({"years": {"$ne": []}})

    log.info("")
    log.info("=" * 65)
    log.info(f"  ✓ hotel_gold      : {final:,} documents")
    log.info(f"  Avec exercices    : {with_years:,}")
    log.info(f"  Sans exercices    : {skipped:,} (pas encore scrapés)")
    log.info(f"  Durée             : {elapsed:.0f}s")
    log.info("=" * 65)

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build hotel_gold — Ratios financiers depuis dépôts NBB"
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Limiter à N entreprises (test)")
    args = parser.parse_args()
    main(limit=args.limit)
