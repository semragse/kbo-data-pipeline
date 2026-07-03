const fmt = new Intl.NumberFormat('fr-BE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 0 })
const pct  = v => v != null ? `${v.toFixed(2)} %` : '—'
const eur  = v => v != null && v !== 0 ? fmt.format(v) : '—'
const ratio = v => v != null ? v.toFixed(4) : '—'

const FIELDS = [
  { key: 'chiffre_affaires',   label: "Chiffre d'affaires", fmt: eur },
  { key: 'achats',             label: 'Achats',             fmt: eur },
  { key: 'variation_stocks',   label: 'Variation stocks',   fmt: eur },
  { key: 'ebit',               label: 'EBIT',               fmt: eur },
  { key: 'resultat_net',       label: 'Résultat net',        fmt: eur },
  { key: 'tresorerie',         label: 'Trésorerie',          fmt: eur },
  { key: 'dettes_financieres', label: 'Dettes financières',  fmt: eur },
  { key: 'fonds_propres',      label: 'Fonds propres',       fmt: eur },
]

const RATIOS = [
  { key: 'marge_brute',          label: 'Marge brute',          fmt: eur },
  { key: 'marge_nette_pct',      label: 'Marge nette',          fmt: pct },
  { key: 'roe_pct',              label: 'ROE',                   fmt: pct },
  { key: 'liquidite',            label: 'Ratio de liquidité',    fmt: ratio },
  { key: 'taux_endettement_pct', label: "Taux d'endettement",   fmt: pct },
]

export default function RatioTable({ years, isHotel }) {
  if (!years || years.length === 0) {
    if (!isHotel)
      return <p style={{ color: '#888' }}>Cette entreprise n'appartient pas au secteur hôtelier (NACE 55xxx).<br />Seuls les hôtels disposent de ratios financiers.</p>
    return <p style={{ color: '#888' }}>Exercices financiers en cours de récupération (scraping NBB).<br />Relancez <code>python build_gold.py</code> une fois le scraping terminé.</p>
  }

  // Vérifier si toutes les valeurs CA sont à 0 (PDFs téléchargés mais pas encore parsés)
  const allZero = years.every(y => !y.chiffre_affaires || y.chiffre_affaires === 0)

  const sorted = [...years].sort((a, b) => b.year - a.year)

  return (
    <div style={{ overflowX: 'auto' }}>
      {allZero && (
        <div style={{ background: '#ddf4ff', border: '1px solid #54aeff', borderRadius: 6, padding: '8px 14px', marginBottom: 12, fontSize: 13 }}>
          ℹ️ Les dépôts NBB ({years.map(y => y.year).join(', ')}) ont été téléchargés en PDF.
          L'extraction des codes PCMN depuis les PDFs nécessite une clé API NBB structurée.
        </div>
      )}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ background: '#f0f4f8' }}>
            <th style={th}>Indicateur</th>
            {sorted.map(y => <th key={y.year} style={th}>{y.year || '—'}</th>)}
          </tr>
        </thead>
        <tbody>
          {FIELDS.map(f => (
            <tr key={f.key}>
              <td style={tdLabel}>{f.label}</td>
              {sorted.map(y => <td key={y.year} style={tdVal}>{f.fmt(y[f.key])}</td>)}
            </tr>
          ))}
          <tr><td colSpan={sorted.length + 1} style={{ height: 8 }} /></tr>
          <tr style={{ background: '#eef6ff' }}>
            <td colSpan={sorted.length + 1} style={{ ...tdLabel, fontWeight: 700 }}>Ratios calculés</td>
          </tr>
          {RATIOS.map(r => (
            <tr key={r.key}>
              <td style={tdLabel}>{r.label}</td>
              {sorted.map(y => <td key={y.year} style={tdVal}>{r.fmt(y.ratios?.[r.key])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const th      = { padding: '8px 12px', textAlign: 'right', borderBottom: '2px solid #d0d7de' }
const tdLabel = { padding: '6px 12px', fontWeight: 600, borderBottom: '1px solid #eee', whiteSpace: 'nowrap' }
const tdVal   = { padding: '6px 12px', textAlign: 'right', borderBottom: '1px solid #eee', fontFamily: 'monospace' }
