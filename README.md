# KBO Data Pipeline — Belgian Enterprise Intelligence

Pipeline de données **Bronze → Silver → Gold** alimenté par le **KBO/BCE Open Data** belge.  
Il ingère les ~2 millions d'entreprises belges dans MongoDB, nettoie les données, puis scrape les dépôts financiers annuels à la **Banque Nationale de Belgique (NBB)** pour cibler le secteur hôtelier.

---

## Architecture du pipeline

```
KBO CSV files (9)
  enterprise.csv
  denomination.csv          ┐
  address.csv               │  streaming merge-join
  activity.csv              ├──────────────────────►  [ Bronze ]
  contact.csv               │  (O(n), ~2M docs)        enterprise_finale
  branch.csv                │
  establishment.csv (trié)  ┘

  [ Bronze ]                                           [ Silver ]
  enterprise_finale  ──── 5 transformations ────────►  enterprise_silver
                          • normalise les dates
                          • déduplique les activités
                          • filtre adresse REGO
                          • remonte dénomination 001
                          • décode codes → labels FR

  [ Silver ]                                           [ Gold ]
  enterprise_silver  ──── NACE hôtellerie ──────────►  scraping NBB
  (filtre 55xxx)          + StateDB                     PDF dépôts 2021-2025
```

---

## Prérequis

| Outil | Version | Rôle |
|---|---|---|
| Python | ≥ 3.10 | Scripts pipeline |
| Podman | ≥ 4.0 | Conteneur MongoDB |
| podman-compose | ≥ 1.0 | Orchestration services |
| Git | ≥ 2.30 | Versioning |

> **Podman Desktop** est l'alternative à Docker recommandée sur Windows.  
> Télécharger : https://podman-desktop.io

---

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/semragse/kbo-data-pipeline.git
cd kbo-data-pipeline
git checkout INGESTION-BRONZE
```

### 2. Installer les dépendances Python

```bash
pip install -r requirements.txt
```

### 3. Configurer l'environnement

```bash
cp .env.example .env
# Éditer .env et renseigner les variables (voir section Variables d'environnement)
```

### 4. Télécharger les données KBO

Télécharger l'archive **KboOpenData** depuis :  
https://statbel.fgov.be/fr/open-data/kbo-open-data

Décompresser les CSV dans le dossier racine du projet (ou adapter `KBO_DIR` dans `.env`).

Fichiers attendus :
```
enterprise.csv
denomination.csv
address.csv
activity.csv
contact.csv
establishment.csv
branch.csv
code.csv
meta.csv
```

---

## Démarrage des services (MongoDB + Mongo Express)

```bash
# Démarrer MongoDB 7.0 + Mongo Express
podman-compose up -d

# Vérifier que les conteneurs tournent
podman ps

# Interface web Mongo Express → http://localhost:8081
# MongoDB             → localhost:27017
```

**Arrêter les services :**
```bash
podman-compose down
```

---

## Variables d'environnement (`.env`)

| Variable | Défaut | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://admin:admin123@localhost:27017/` | URI de connexion MongoDB |
| `MONGO_DB` | `kbo_bronze` | Nom de la base de données |
| `KBO_DIR` | `.` | Dossier contenant les CSV KBO |
| `NBB_API_KEY` | *(vide)* | Clé API NBB CBSO (gratuite sur https://developer.cbso.nbb.be) |
| `NBB_YEARS` | `2021,2022,2023,2024,2025` | Années à scraper |
| `NBB_OUTPUT_DIR` | `tmp/nbb_deposits` | Dossier de sortie PDF/JSON |

---

## Exécution du pipeline

### Étape 1 — Couche Bronze : ingestion CSV → MongoDB

```bash
# Ingestion complète (~2M entreprises, ~10-15 min selon matériel)
python ingest_bronze.py

# Test rapide sur 5 000 entreprises
python ingest_bronze.py --limit 5000
```

Ce script :
- Lit les 7 CSV en **streaming merge-join** (mémoire constante)
- Crée **une seule collection** `enterprise_finale` dans `kbo_bronze`
- Chaque document = **une entreprise complète** avec toutes ses données jointes
- Crée 5 index pour les requêtes Silver/Gold

**Résultat attendu :** ~1 951 000 documents dans `enterprise_finale`

---

### Étape 2 — Couche Silver : nettoyage et enrichissement

```bash
python build_silver.py
```

Ce script applique 5 transformations sur `enterprise_finale` et écrit dans `enterprise_silver` :

| # | Transformation | Détail |
|---|---|---|
| 1 | **Normalisation dates** | `DD-MM-YYYY` → `YYYY-MM-DD` |
| 2 | **Déduplication activités** | Même `(NaceCode, Classification)` → 1 seul enregistrement |
| 3 | **Adresse principale** | Ne garder que `TypeOfAddress = REGO` |
| 4 | **Dénomination principale** | `TypeOfDenomination = 001` remontée en tête de liste |
| 5 | **Décodage codes → labels** | `JuridicalForm`, `Status`, `NaceCode` traduits en libellés FR |

---

### Étape 3 — Gold : scraping NBB pour le secteur hôtelier

```bash
# Étape 3a : initialiser la StateDB (filtrage NACE hôtellerie)
python scraping_nbb.py --init

# Étape 3b : lancer le scraping des dépôts NBB
python scraping_nbb.py --scrape

# Tout en une commande
python scraping_nbb.py --init --scrape
```

**Codes NACE ciblés (hôtellerie) :**
`55100, 55201, 55202, 55203, 55204, 55209, 55300, 55400, 55900`

Ce script :
1. Filtre les entreprises hôtelières dans `enterprise_silver`
2. Exclut les formes juridiques non commerciales (ASBL, fondations, etc.)
3. Stocke l'état de scraping dans la collection `state_nbb` (pending/done/error)
4. Appelle l'API NBB CBSO pour télécharger les dépôts 2021-2025
5. Gère le rate-limit HTTP 429 avec backoff automatique (max 5 retries)

---

## Structure MongoDB

### Collection `enterprise_finale` (Bronze)

```json
{
  "_id": "0878.065.378",
  "EnterpriseNumber": "0878.065.378",
  "Status": "AC",
  "JuridicalSituation": "AC",
  "TypeOfEnterprise": "2",
  "JuridicalForm": "416",
  "JuridicalFormCAC": null,
  "StartDate": "01-01-2005",
  "denominations": [
    { "Language": "FR", "TypeOfDenomination": "001", "Denomination": "HOTEL EXAMPLE SA" }
  ],
  "addresses": [
    { "TypeOfAddress": "REGO", "Zipcode": "1000", "MunicipalityFR": "Bruxelles", "StreetFR": "Rue de la Loi", "HouseNumber": "42" }
  ],
  "activities": [
    { "ActivityGroup": "MAIN", "NaceVersion": "2008", "NaceCode": "55100", "Classification": "NACE_MAIN" }
  ],
  "contacts": [
    { "EntityContact": "0878.065.378", "ContactType": "WEB", "Value": "https://hotel-example.be" }
  ],
  "establishments": [
    { "EstablishmentNumber": "2.229.987.345", "StartDate": "01-01-2005" }
  ],
  "branches": []
}
```

### Collection `enterprise_silver` (Silver)

Même structure qu'en Bronze, avec en plus :
- Dates au format `YYYY-MM-DD`
- Champs `JuridicalFormLabel`, `StatusLabel`, `NaceLabel` (libellés décodés)
- Activités dédupliquées
- Liste d'adresses filtrée sur REGO uniquement

### Collection `state_nbb` (Gold StateDB)

```json
{
  "_id": "0878.065.378",
  "status": "done",
  "name": "HOTEL EXAMPLE SA",
  "nace_codes": ["55100"],
  "updated_at": "2025-01-15T10:30:00",
  "deposits": { "2021": "...", "2022": "..." }
}
```

---

## Structure du dépôt

```
kbo-data-pipeline/
├── docker-compose.yml        # MongoDB 7.0 + Mongo Express (podman-compose)
├── ingest_bronze.py          # ★ Ingestion CSV → enterprise_finale (Bronze)
├── build_silver.py           # Transformations → enterprise_silver (Silver)
├── scraping_nbb.py           # Scraping NBB dépôts hôtels (Gold)
├── consult.py                # Utilitaire scraping NBB (interface web)
├── strapor.py                # Scraping statuts notariaux (Playwright)
├── requirements.txt          # Dépendances Python
├── .env.example              # Template variables d'environnement
├── .gitignore                # Exclusions Git (CSV, .env, PDF…)
└── README.md                 # Ce fichier
```

---

## Branches Git

| Branche | Contenu |
|---|---|
| `INGESTION-BRONZE` | `ingest_bronze.py` + infrastructure (docker-compose, requirements) |
| `main` | Pipeline complet (Bronze + Silver + Gold) |

---

## Sources de données

| Source | URL |
|---|---|
| KBO/BCE Open Data | https://statbel.fgov.be/fr/open-data/kbo-open-data |
| API NBB CBSO | https://developer.cbso.nbb.be |
| Documentation KBO | https://economie.fgov.be/fr/themes/entreprises/banque-carrefour-des/open-data |

---

## Auteur

Projet réalisé dans le cadre du cours **Data Engineering** — IPSSI (2025-2026).
