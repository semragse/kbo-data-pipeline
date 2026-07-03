"""
API FastAPI — KBO Hotel Intelligence
=====================================
Expose les données Gold/Silver au frontend React.

Endpoints :
  GET  /api/search?q=...          Recherche entreprises (nom ou BCE)
  GET  /api/entreprise/{num}      Fiche complète Silver + ratios Gold
  GET  /api/hotels                Liste des hôtels (hotel_gold)
  GET  /api/statuts/{num}         SSE — streaming statuts notaire
  GET  /health                    Healthcheck

Usage :
  pip install fastapi uvicorn motor
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URI", "mongodb://admin:admin123@localhost:27017/")
DB_NAME   = os.getenv("MONGO_DB",  "kbo_bronze")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="KBO Hotel Intelligence API",
    description="Données Bronze/Silver/Gold KBO — secteur hôtelier belge",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── MongoDB (Motor async) ──────────────────────────────────────────────────────

_client: AsyncIOMotorClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5_000)
    return _client[DB_NAME]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean_doc(doc: dict) -> dict:
    """Convertit ObjectId en str pour la sérialisation JSON."""
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _bce_normalize(q: str) -> str:
    """Normalise un numéro BCE : 0878065378 → 0878.065.378"""
    digits = re.sub(r"\D", "", q)
    if len(digits) == 10:
        return f"{digits[:4]}.{digits[4:7]}.{digits[7:]}"
    return q


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    try:
        db = get_db()
        await db.command("ping")
        silver_count = await db["enterprise_silver"].count_documents({})
        gold_count   = await db["hotel_gold"].count_documents({})
        return {
            "status":         "ok",
            "enterprise_silver": silver_count,
            "hotel_gold":        gold_count,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=2, description="Nom ou numéro BCE"),
    limit: int = Query(20, le=100),
):
    """
    Recherche dans enterprise_silver par nom (regex) ou numéro BCE exact.
    Retourne une liste légère pour la barre de recherche.
    """
    db  = get_db()
    col = db["enterprise_silver"]

    # Essayer d'abord comme numéro BCE
    bce = _bce_normalize(q)
    if re.match(r"^\d{4}\.\d{3}\.\d{3}$", bce):
        doc = await col.find_one(
            {"EnterpriseNumber": bce},
            {"EnterpriseNumber": 1, "denominations": 1,
             "JuridicalFormLabel": 1, "Status": 1, "addresses": 1}
        )
        if doc:
            return {"results": [_format_search_result(_clean_doc(doc))]}

    # Sinon recherche textuelle sur les dénominations
    regex   = {"$regex": q, "$options": "i"}
    query   = {"denominations.Denomination": regex, "Status": "AC"}
    proj    = {"EnterpriseNumber": 1, "denominations": 1,
               "JuridicalFormLabel": 1, "Status": 1, "addresses": 1}

    cursor  = col.find(query, proj).limit(limit)
    results = []
    async for doc in cursor:
        results.append(_format_search_result(_clean_doc(doc)))

    return {"results": results, "count": len(results)}


def _format_search_result(doc: dict) -> dict:
    """Extrait les champs essentiels pour la liste de résultats."""
    name = None
    for d in doc.get("denominations", []):
        if d.get("Denomination"):
            name = d["Denomination"]
            break

    address = None
    for a in doc.get("addresses", []):
        parts = [a.get("Zipcode"), a.get("MunicipalityFR") or a.get("MunicipalityNL")]
        address = " ".join(p for p in parts if p)
        break

    return {
        "enterprise_number": doc.get("EnterpriseNumber"),
        "name":              name,
        "juridical_form":    doc.get("JuridicalFormLabel"),
        "status":            doc.get("Status"),
        "address":           address,
    }


@app.get("/api/entreprise/{enterprise_number}")
async def get_entreprise(enterprise_number: str):
    """
    Fiche complète d'une entreprise :
    - Données Silver (infos, adresse, activités, contacts)
    - Ratios Gold (exercices financiers)
    """
    db  = get_db()
    num = _bce_normalize(enterprise_number)

    # Silver
    silver = await db["enterprise_silver"].find_one(
        {"EnterpriseNumber": num}
    )
    if not silver:
        raise HTTPException(status_code=404, detail=f"Entreprise {num} introuvable")
    silver = _clean_doc(silver)

    # Gold
    gold = await db["hotel_gold"].find_one(
        {"enterprise_number": num},
        {"_id": 0}
    )

    return {
        "silver": silver,
        "gold":   gold,
    }


@app.get("/api/hotels")
async def list_hotels(
    limit:  int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    nace:   str = Query(None, description="Filtrer par code NACE (ex: 55100)"),
):
    """
    Liste des hôtels dans hotel_gold avec pagination.
    """
    db    = get_db()
    query = {}
    if nace:
        query["nace_codes"] = nace

    total  = await db["hotel_gold"].count_documents(query)
    cursor = db["hotel_gold"].find(
        query,
        {"enterprise_number": 1, "name": 1, "nace_codes": 1,
         "schema_type": 1, "years": {"$slice": -1}}  # dernier exercice seulement
    ).skip(offset).limit(limit)

    items = []
    async for doc in cursor:
        items.append(_clean_doc(doc))

    return {"total": total, "offset": offset, "limit": limit, "items": items}


@app.get("/api/statuts/{enterprise_number}")
async def stream_statuts(enterprise_number: str):
    """
    SSE — Streaming des statuts notariaux depuis strapor.py.
    Chaque événement SSE contient un document notarial.
    """
    num = _bce_normalize(enterprise_number)

    async def event_generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'status': 'start', 'enterprise_number': num})}\n\n"

        try:
            # Vérifier si déjà en base
            db    = get_db()
            saved = await db["statuts_notaire"].find_one({"enterprise_number": num})

            if saved and saved.get("documents"):
                # Servir depuis le cache MongoDB
                for doc_notaire in saved["documents"]:
                    payload = json.dumps({"type": "document", "data": doc_notaire},
                                         ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    await asyncio.sleep(0.05)
            else:
                # Lancer le scraper en subprocess et streamer les résultats
                proc = await asyncio.create_subprocess_exec(
                    "python", "strapor.py", "--bce", num,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                documents = []
                async for line in proc.stdout:
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue
                    try:
                        item = json.loads(line_str)
                        documents.append(item)
                        payload = json.dumps({"type": "document", "data": item},
                                              ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                    except json.JSONDecodeError:
                        pass

                await proc.wait()

                # Persister en base
                if documents:
                    await db["statuts_notaire"].update_one(
                        {"enterprise_number": num},
                        {"$set": {
                            "enterprise_number": num,
                            "documents":         documents,
                        }},
                        upsert=True,
                    )

        except Exception as e:
            yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

        yield f"data: {json.dumps({'status': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )
