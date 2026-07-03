import { useState } from 'react'
import RatioTable   from './RatioTable'
import SankeyChart  from './SankeyChart'
import StatutsStream from './StatutsStream'

export default function EntreprisePage({ data, onBack }) {
  const { silver, gold } = data
  const [selectedYear, setSelectedYear] = useState(
    gold?.years?.at(-1)?.year || null
  )

  // Nom principal
  const name = silver?.denominations?.[0]?.Denomination || gold?.name || '—'
  const num  = silver?.EnterpriseNumber || gold?.enterprise_number

  // Adresse REGO
  const addr = silver?.addresses?.[0]

  // Activité principale
  const mainActivity = silver?.activities?.find(a => a.Classification === 'MAIN')

  const years    = gold?.years || []
  const isHotel  = gold !== null && gold !== undefined   // présent dans hotel_gold = c'est un hôtel

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '24px 16px' }}>
      {/* Retour */}
      <button onClick={onBack} style={backBtn}>← Retour</button>

      {/* En-tête entreprise */}
      <div style={card}>
        <h2 style={{ margin: '0 0 8px', fontSize: 22 }}>{name}</h2>
        <div style={{ color: '#555', fontSize: 14, lineHeight: 1.8 }}>
          <div><b>N° BCE :</b> {num}</div>
          <div><b>Statut :</b> {silver?.StatusLabel || silver?.Status || '—'}</div>
          <div><b>Forme juridique :</b> {silver?.JuridicalFormLabel || silver?.JuridicalForm || '—'}</div>
          <div><b>Date de début :</b> {silver?.StartDate || '—'}</div>
          {addr && (
            <div>
              <b>Adresse :</b>{' '}
              {[addr.StreetFR, addr.HouseNumber, addr.Zipcode,
                addr.MunicipalityFR || addr.MunicipalityNL]
                .filter(Boolean).join(' ')}
            </div>
          )}
          {mainActivity && (
            <div>
              <b>Activité :</b> {mainActivity.NaceLabel || mainActivity.NaceCode}
            </div>
          )}
        </div>
      </div>

      {/* Badge hôtel / non hôtel */}
      {!isHotel && (
        <div style={{ background: '#fff8c5', border: '1px solid #d4a017', borderRadius: 6, padding: '10px 16px', marginBottom: 16, fontSize: 14 }}>
          ⚠️ Cette entreprise n'est pas répertoriée comme hôtel (NACE 55xxx). Les données financières ne sont pas disponibles.
        </div>
      )}

      {/* Sankey */}
      {years.length > 0 && (
        <div style={card}>
          <h3 style={cardTitle}>Compte de résultats — Sankey</h3>
          {years.length > 1 && (
            <div style={{ marginBottom: 12 }}>
              <label style={{ marginRight: 8, fontSize: 14 }}>Exercice :</label>
              <select
                value={selectedYear}
                onChange={e => setSelectedYear(Number(e.target.value))}
                style={{ padding: '4px 8px' }}
              >
                {[...years].sort((a, b) => b.year - a.year).map(y => (
                  <option key={y.year} value={y.year}>{y.year}</option>
                ))}
              </select>
            </div>
          )}
          <SankeyChart years={years} selectedYear={selectedYear} />
        </div>
      )}

      {/* Tableau des ratios */}
      <div style={card}>
        <h3 style={cardTitle}>Ratios financiers par exercice</h3>
        <RatioTable years={years} isHotel={isHotel} />
      </div>

      {/* Contacts */}
      {silver?.contacts?.length > 0 && (
        <div style={card}>
          <h3 style={cardTitle}>Contacts</h3>
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {silver.contacts.map((c, i) => (
              <li key={i} style={{ fontSize: 14, marginBottom: 4 }}>
                <b>{c.ContactType} :</b>{' '}
                {c.ContactType === 'WEB'
                  ? <a href={c.Value} target="_blank" rel="noreferrer">{c.Value}</a>
                  : c.Value}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Statuts notariaux SSE */}
      <div style={card}>
        <h3 style={cardTitle}>Statuts notariaux</h3>
        <StatutsStream enterpriseNumber={num} />
      </div>
    </div>
  )
}

const card     = { background: '#fff', border: '1px solid #d0d7de', borderRadius: 8, padding: '20px 24px', marginBottom: 20 }
const cardTitle = { margin: '0 0 16px', fontSize: 16, fontWeight: 700, color: '#24292f' }
const backBtn  = {
  background: 'none', border: '1px solid #d0d7de', borderRadius: 6,
  padding: '6px 14px', cursor: 'pointer', marginBottom: 20, fontSize: 14,
}
