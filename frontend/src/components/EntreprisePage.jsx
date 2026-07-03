import { useState } from 'react'
import RatioTable    from './RatioTable'
import SankeyChart   from './SankeyChart'
import StatutsStream from './StatutsStream'

// ── helpers ──────────────────────────────────────────────────────────────────
const JURIDICAL_SITUATION = {
  '000': 'Situation normale',
  '001': 'En faillite',
  '002': 'En liquidation',
  '003': 'Dissolution judiciaire',
  '005': 'Continuité (loi 31/01/2009)',
  '006': 'Dissolution volontaire',
  '009': 'Radiation',
}

const CONTACT_LABEL = { TEL: '📞 Téléphone', FAX: '📠 Fax', EMAIL: '✉️ Email', WEB: '🌐 Site web' }

function bceToTva(num) {
  if (!num) return '—'
  return 'BE' + num.replace(/\./g, '')
}

function Row({ label, value }) {
  if (!value && value !== 0) return null
  return (
    <div style={{ display: 'flex', gap: 12, padding: '5px 0', borderBottom: '1px solid #f0f0f0', fontSize: 14 }}>
      <span style={{ minWidth: 220, color: '#555', fontWeight: 600 }}>{label}</span>
      <span style={{ color: '#1a1a1a' }}>{value}</span>
    </div>
  )
}

// ── composant principal ───────────────────────────────────────────────────────
export default function EntreprisePage({ data, onBack }) {
  const { silver, gold, linked } = data
  const [selectedYear, setSelectedYear] = useState(
    gold?.years?.at(-1)?.year || null
  )

  const name = silver?.denominations?.[0]?.Denomination || gold?.name || '—'
  const num  = silver?.EnterpriseNumber || gold?.enterprise_number
  const addr = silver?.addresses?.find(a => a.TypeOfAddress === 'REGO') || silver?.addresses?.[0]

  const years   = gold?.years || []
  const isHotel = gold !== null && gold !== undefined

  // Dernière année avec données complètes
  const lastYear = [...years].reverse().find(y => y.capital_souscrit || y.general_assembly)

  // Activités : MAIN d'abord, puis SECO
  const activities = (silver?.activities || [])
    .filter((a, i, arr) => arr.findIndex(x => x.NaceCode === a.NaceCode && x.NaceVersion === a.NaceVersion) === i)
    .sort((a, b) => (a.Classification === 'MAIN' ? -1 : 1))

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '24px 16px' }}>
      <button onClick={onBack} style={backBtn}>← Retour</button>

      {/* ── En-tête ── */}
      <div style={{ ...card, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h2 style={{ margin: '0 0 4px', fontSize: 22 }}>{name}</h2>
          <div style={{ fontSize: 13, color: '#888' }}>
            {silver?.JuridicalFormLabel || '—'} · {silver?.StatusLabel || silver?.Status || '—'}
          </div>
        </div>
        {isHotel && (
          <span style={{ background: '#d1fae5', color: '#065f46', borderRadius: 20, padding: '4px 14px', fontSize: 13, fontWeight: 600 }}>
            🏨 Secteur hôtelier
          </span>
        )}
      </div>

      {!isHotel && (
        <div style={{ background: '#fff8c5', border: '1px solid #d4a017', borderRadius: 6, padding: '10px 16px', marginBottom: 16, fontSize: 14 }}>
          ⚠️ Cette entreprise n'est pas répertoriée comme hôtel (NACE 55xxx). Les données financières ne sont pas disponibles.
        </div>
      )}

      {/* ── Informations juridiques ── */}
      <div style={card}>
        <h3 style={cardTitle}>Informations juridiques</h3>
        <Row label="Numéro BCE (équiv. SIRET belge)" value={num} />
        <Row label="Numéro de TVA" value={bceToTva(num)} />
        <Row label="Forme juridique" value={silver?.JuridicalFormLabel} />
        <Row label="Situation juridique"
             value={JURIDICAL_SITUATION[silver?.JuridicalSituation] || silver?.JuridicalSituation || 'Situation normale'} />
        <Row label="Statut" value={silver?.StatusLabel || silver?.Status} />
        <Row label="Date de constitution" value={silver?.StartDate} />
        {addr && (
          <Row label="Adresse (siège REGO)"
               value={[addr.StreetFR, addr.HouseNumber, addr.Zipcode, addr.MunicipalityFR || addr.MunicipalityNL].filter(Boolean).join(' ')} />
        )}
        {lastYear && <>
          <Row label="Capital souscrit" value={lastYear.capital_souscrit != null ? new Intl.NumberFormat('fr-BE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }).format(lastYear.capital_souscrit) : null} />
          <Row label="Assemblée générale" value={lastYear.general_assembly} />
          <Row label="Fin de l'exercice comptable" value={lastYear.period_end?.slice(0, 10)} />
        </>}
        <div style={{ marginTop: 10, padding: '8px 12px', background: '#f8f9fa', borderRadius: 4, fontSize: 12, color: '#888' }}>
          ℹ️ Les dirigeants et représentants ne sont pas publiés dans le KBO Open Data — ils figurent dans les actes au Moniteur belge (section Statuts notariaux ci-dessous).
        </div>
      </div>

      {/* ── Activités NACE ── */}
      {activities.length > 0 && (
        <div style={card}>
          <h3 style={cardTitle}>Activités (codes NACE)</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#f0f4f8' }}>
                <th style={th}>Code</th>
                <th style={th}>Libellé</th>
                <th style={th}>Classification</th>
                <th style={th}>Version</th>
              </tr>
            </thead>
            <tbody>
              {activities.map((a, i) => (
                <tr key={i} style={{ background: a.Classification === 'MAIN' ? '#f0fdf4' : 'white' }}>
                  <td style={td}><code style={{ fontSize: 12 }}>{a.NaceCode}</code></td>
                  <td style={td}>{a.NaceLabel || '—'}</td>
                  <td style={td}>
                    {a.Classification === 'MAIN'
                      ? <span style={{ color: '#065f46', fontWeight: 700 }}>Principale</span>
                      : <span style={{ color: '#888' }}>Secondaire</span>}
                  </td>
                  <td style={td}>{a.NaceVersion}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Établissements & liens ── */}
      {(silver?.establishments?.length > 0 || silver?.branches?.length > 0) && (
        <div style={card}>
          <h3 style={cardTitle}>Établissements & liens entre entités</h3>
          {silver.establishments?.length > 0 && (
            <>
              <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Unités d'établissement ({silver.establishments.length})</p>
              {silver.establishments.map((e, i) => (
                <div key={i} style={{ fontSize: 13, padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
                  📍 <b>{e.EstablishmentNumber}</b>
                  {e.StartDate && ` — actif depuis ${e.StartDate}`}
                </div>
              ))}
            </>
          )}
          {silver.branches?.length > 0 && (
            <>
              <p style={{ fontSize: 13, fontWeight: 600, margin: '12px 0 8px' }}>Succursales ({silver.branches.length})</p>
              {silver.branches.map((b, i) => (
                <div key={i} style={{ fontSize: 13, padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
                  🔗 {b.EnterpriseNumber || JSON.stringify(b)}
                </div>
              ))}
            </>
          )}
          <div style={{ marginTop: 10, fontSize: 12, color: '#888' }}>
            ℹ️ Le nombre d'employés n'est pas publié dans le KBO Open Data belge.
          </div>
        </div>
      )}

      {/* ── Sankey ── */}
      {years.length > 0 && (
        <div style={card}>
          <h3 style={cardTitle}>Compte de résultats — Sankey</h3>
          {years.length > 1 && (
            <div style={{ marginBottom: 12 }}>
              <label style={{ marginRight: 8, fontSize: 14 }}>Exercice :</label>
              <select value={selectedYear} onChange={e => setSelectedYear(Number(e.target.value))} style={{ padding: '4px 8px' }}>
                {[...years].sort((a, b) => b.year - a.year).map(y => (
                  <option key={y.year} value={y.year}>{y.year}</option>
                ))}
              </select>
            </div>
          )}
          <SankeyChart years={years} selectedYear={selectedYear} />
        </div>
      )}

      {/* ── Ratios financiers ── */}
      <div style={card}>
        <h3 style={cardTitle}>Ratios financiers par exercice</h3>
        <RatioTable years={years} isHotel={isHotel} />
      </div>

      {/* ── Contacts ── */}
      {silver?.contacts?.length > 0 && (
        <div style={card}>
          <h3 style={cardTitle}>Contacts</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {silver.contacts.map((c, i) => (
              <div key={i} style={{ fontSize: 14 }}>
                <span style={{ marginRight: 8 }}>{CONTACT_LABEL[c.ContactType] || c.ContactType} :</span>
                {c.ContactType === 'WEB' || c.ContactType === 'EMAIL'
                  ? <a href={c.ContactType === 'EMAIL' ? `mailto:${c.Value}` : c.Value}
                       target="_blank" rel="noreferrer">{c.Value}</a>
                  : <span>{c.Value}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Statuts notariaux ── */}
      <div style={card}>
        <h3 style={cardTitle}>Statuts notariaux (Moniteur belge)</h3>
        <StatutsStream enterpriseNumber={num} />
      </div>
    </div>
  )
}

const card     = { background: '#fff', border: '1px solid #d0d7de', borderRadius: 8, padding: '20px 24px', marginBottom: 20 }
const cardTitle = { margin: '0 0 16px', fontSize: 16, fontWeight: 700, color: '#24292f' }
const backBtn  = { background: 'none', border: '1px solid #d0d7de', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', marginBottom: 20, fontSize: 14 }
const th       = { padding: '8px 12px', textAlign: 'left', borderBottom: '2px solid #d0d7de', fontWeight: 600 }
const td       = { padding: '7px 12px', borderBottom: '1px solid #eee' }
