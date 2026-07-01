"""
INGESTION BRONZE — KBO Open Data CSV → MongoDB (enterprise_finale)
===================================================================
Lit les CSV KBO Open Data et produit directement UNE SEULE collection
`enterprise_finale` : un document complet par entreprise contenant
TOUTES les informations issues de tous les CSV (jointure sur
EnterpriseNumber / EntityNumber).

Structure de chaque document :
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

Stratégie mémoire (streaming merge-join) :
  Les fichiers enterprise/denomination/address/activity/contact/branch
  sont triés par numéro d'entité → jointure par fusion en O(n), mémoire
  constante même avec 34 M lignes d'activités.
  establishment.csv n'est pas trié → pré-trié en mémoire une seule fois.

Usage :
  python ingest_bronze.py
  python ingest_bronze.py --limit 5000   # test sur un échantillon
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
from tqdm import tqdm

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────

MONGO_URI  = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME    = os.getenv("MONGO_DB",  "kbo_bronze")
DATA_DIR   = Path(os.getenv("KBO_DIR", str(Path(__file__).parent)))
COLLECTION = "enterprise_finale"
BATCH_SIZE = 1_000

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Colonnes conservées par table enfant ───────────────────────────────────────

_DENOM_COLS  = ["Language", "TypeOfDenomination", "Denomination"]
_ADDR_COLS   = [
    "TypeOfAddress", "CountryNL", "CountryFR", "Zipcode",
    "MunicipalityNL", "MunicipalityFR", "StreetNL", "StreetFR",
    "HouseNumber", "Box", "ExtraAddressInfo", "DateStrikingOff",
]
_ACT_COLS    = ["ActivityGroup", "NaceVersion", "NaceCode", "Classification"]
_CONT_COLS   = ["EntityContact", "ContactType", "Value"]
_ESTAB_COLS  = ["EstablishmentNumber", "StartDate"]
_BRANCH_COLS = ["StartDate"]


# ── Streaming child reader (merge-join) ────────────────────────────────────────

class _ChildStream:
    """
    Lit un CSV enfant trié par clé et renvoie via take(target)
    toutes les lignes dont la clé == target, en avançant le curseur.
    Les clés orphelines (< target) sont ignorées silencieusement.
    """

    def __init__(self, path: Path, key: str, cols: list[str]):
        self._f      = open(path, newline="", encoding="utf-8")
        self._reader = csv.DictReader(self._f)
        self._key    = key
        self._cols   = cols
        self._cur: dict | None = None
        self._advance()

    def _advance(self) -> None:
        self._cur = next(self._reader, None)

    @staticmethod
    def _clean(v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None

    def take(self, target: str) -> list[dict]:
        # Sauter les orphelins (clé < target)
        while self._cur is not None and self._cur[self._key] < target:
            self._advance()
        rows: list[dict] = []
        while self._cur is not None and self._cur[self._key] == target:
            rows.append({c: self._clean(self._cur.get(c)) for c in self._cols})
            self._advance()
        return rows

    def close(self) -> None:
        self._f.close()


# ── Pré-tri de establishment.csv ───────────────────────────────────────────────

def _sort_establishments(src: Path, tmp_dir: str) -> Path:
    """
    establishment.csv n'est pas trié par EnterpriseNumber.
    On le trie une fois dans un fichier temporaire pour pouvoir
    le consommer en merge-join (mémoire constante pendant l'ingestion).
    """
    log.info("  Pré-tri establishment.csv par EnterpriseNumber...")
    rows: list[tuple[str, str, str]] = []
    with open(src, newline="", encoding="utf-8") as f:
        for row in tqdm(csv.DictReader(f), desc="    tri établissements",
                        unit=" lignes", leave=False):
            rows.append((
                row.get("EnterpriseNumber", ""),
                row.get("EstablishmentNumber", "") or "",
                row.get("StartDate", "") or "",
            ))
    rows.sort(key=lambda r: r[0])
    out = Path(tmp_dir) / "establishment_sorted.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["EnterpriseNumber", "EstablishmentNumber", "StartDate"])
        writer.writerows(rows)
    rows.clear()   # libère la mémoire avant la fusion principale
    return out


# ── Générateur de documents Bronze ────────────────────────────────────────────

def _iter_documents(data_dir: Path, tmp_dir: str,
                    limit: int | None) -> Iterator[dict]:
    """Parcourt enterprise.csv et joint toutes les tables enfants en streaming."""

    est_sorted = _sort_establishments(data_dir / "establishment.csv", tmp_dir)

    denom  = _ChildStream(data_dir / "denomination.csv",  "EntityNumber",     _DENOM_COLS)
    addr   = _ChildStream(data_dir / "address.csv",       "EntityNumber",     _ADDR_COLS)
    act    = _ChildStream(data_dir / "activity.csv",      "EntityNumber",     _ACT_COLS)
    cont   = _ChildStream(data_dir / "contact.csv",       "EntityNumber",     _CONT_COLS)
    estab  = _ChildStream(est_sorted, "EnterpriseNumber", _ESTAB_COLS)
    branch = _ChildStream(data_dir / "branch.csv",        "EnterpriseNumber", _BRANCH_COLS)

    def _c(v: str | None) -> str | None:
        return v.strip() or None if v else None

    try:
        with open(data_dir / "enterprise.csv", newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if limit is not None and i >= limit:
                    break
                num = row["EnterpriseNumber"]
                yield {
                    "_id":                num,
                    "EnterpriseNumber":   num,
                    "Status":             _c(row.get("Status")),
                    "JuridicalSituation": _c(row.get("JuridicalSituation")),
                    "TypeOfEnterprise":   _c(row.get("TypeOfEnterprise")),
                    "JuridicalForm":      _c(row.get("JuridicalForm")),
                    "JuridicalFormCAC":   _c(row.get("JuridicalFormCAC")),
                    "StartDate":          _c(row.get("StartDate")),
                    "denominations":      denom.take(num),
                    "addresses":          addr.take(num),
                    "activities":         act.take(num),
                    "contacts":           cont.take(num),
                    "establishments":     estab.take(num),
                    "branches":           branch.take(num),
                }
    finally:
        for stream in (denom, addr, act, cont, estab, branch):
            stream.close()


# ── Programme principal ────────────────────────────────────────────────────────

def main(limit: int | None = None) -> None:
    log.info("=" * 65)
    log.info("  INGESTION BRONZE — KBO CSV → enterprise_finale (MongoDB)")
    log.info("=" * 65)
    log.info(f"  Source  : {DATA_DIR.resolve()}")
    log.info(f"  MongoDB : {DB_NAME}.{COLLECTION}")
    if limit:
        log.info(f"  Limite  : {limit:,} entreprises (mode test)")
    log.info("")

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    try:
        client.admin.command("ping")
        log.info("✓ MongoDB accessible")
    except Exception as e:
        log.error(f"✗ MongoDB inaccessible : {e}")
        log.error("  → podman-compose up -d")
        return

    db   = client[DB_NAME]
    coll = db[COLLECTION]

    log.info(f"Suppression de '{COLLECTION}' existante (rebuild propre)...")
    coll.drop()
    log.info("")

    start = time.time()
    total = 0
    batch: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="kbo_bronze_") as tmp:
        for doc in tqdm(
            _iter_documents(DATA_DIR, tmp, limit),
            desc="  entreprises",
            unit=" doc",
        ):
            batch.append(doc)
            if len(batch) >= BATCH_SIZE:
                coll.insert_many(batch, ordered=False)
                total += len(batch)
                batch.clear()

        if batch:
            coll.insert_many(batch, ordered=False)
            total += len(batch)

    log.info("Création des index...")
    coll.create_index([("Status",                    ASCENDING)])
    coll.create_index([("TypeOfEnterprise",          ASCENDING)])
    coll.create_index([("JuridicalForm",             ASCENDING)])
    coll.create_index([("activities.NaceCode",       ASCENDING)])
    coll.create_index([("activities.Classification", ASCENDING)])

    elapsed = time.time() - start
    log.info("")
    log.info("=" * 65)
    log.info(f"  ✓ {COLLECTION} : {total:,} documents")
    log.info(f"  Durée : {elapsed:.0f}s")
    log.info("=" * 65)

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingestion Bronze KBO — CSV → enterprise_finale (MongoDB)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Nombre max d'entreprises à ingérer (utile pour tester)"
    )
    args = parser.parse_args()
    main(limit=args.limit)

