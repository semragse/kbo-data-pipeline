import { useState, useCallback } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { searchEntreprises, setQuery, fetchEntreprise, clearEntreprise } from '../store'
import EntreprisePage from './EntreprisePage'

export default function SearchPage() {
  const dispatch = useDispatch()
  const { results, loading, query } = useSelector(s => s.search)
  const { current, loading: loadingFiche } = useSelector(s => s.entreprise)
  const [timer, setTimer] = useState(null)

  const handleInput = useCallback((e) => {
    const q = e.target.value
    dispatch(setQuery(q))
    clearTimeout(timer)
    if (q.length >= 2) {
      const t = setTimeout(() => dispatch(searchEntreprises(q)), 350)
      setTimer(t)
    }
  }, [dispatch, timer])

  const handleSelect = (num) => {
    dispatch(fetchEntreprise(num))
  }

  const handleBack = () => {
    dispatch(clearEntreprise())
  }

  if (current) return <EntreprisePage data={current} onBack={handleBack} />

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: '32px 16px' }}>
      <h1 style={{ fontSize: 24, marginBottom: 24 }}>
        🏨 KBO Hotel Intelligence
      </h1>

      <input
        type="text"
        value={query}
        onChange={handleInput}
        placeholder="Rechercher par nom ou numéro BCE (ex: 0878.065.378)"
        style={inputStyle}
        autoFocus
      />

      {loading && <p style={{ color: '#888', marginTop: 8 }}>Recherche…</p>}

      {results.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, marginTop: 12 }}>
          {results.map(r => (
            <li
              key={r.enterprise_number}
              onClick={() => handleSelect(r.enterprise_number)}
              style={resultItem}
            >
              <div style={{ fontWeight: 600, fontSize: 15 }}>{r.name || '—'}</div>
              <div style={{ fontSize: 12, color: '#666', marginTop: 2 }}>
                {r.enterprise_number}
                {r.juridical_form && ` · ${r.juridical_form}`}
                {r.address && ` · ${r.address}`}
              </div>
            </li>
          ))}
        </ul>
      )}

      {!loading && query.length >= 2 && results.length === 0 && (
        <p style={{ color: '#888', marginTop: 12 }}>Aucun résultat pour « {query} »</p>
      )}

      {loadingFiche && <p style={{ marginTop: 24 }}>Chargement de la fiche…</p>}
    </div>
  )
}

const inputStyle = {
  width: '100%', padding: '12px 16px', fontSize: 15,
  border: '1px solid #d0d7de', borderRadius: 8,
  outline: 'none', boxSizing: 'border-box',
}

const resultItem = {
  padding: '12px 16px', borderBottom: '1px solid #eee',
  cursor: 'pointer', transition: 'background 0.15s',
  ':hover': { background: '#f6f8fa' },
}
