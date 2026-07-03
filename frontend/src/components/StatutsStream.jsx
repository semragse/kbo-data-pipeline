import { useState, useEffect, useRef } from 'react'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

/**
 * Composant qui ouvre une connexion SSE vers /api/statuts/{num}
 * et affiche les documents notariaux au fur et à mesure.
 */
export default function StatutsStream({ enterpriseNumber }) {
  const [docs,    setDocs]    = useState([])
  const [loading, setLoading] = useState(false)
  const [started, setStarted] = useState(false)
  const esRef = useRef(null)

  const start = () => {
    if (esRef.current) esRef.current.close()
    setDocs([])
    setLoading(true)
    setStarted(true)

    const es = new EventSource(`${API}/api/statuts/${enterpriseNumber}`)
    esRef.current = es

    es.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.status === 'done' || msg.status === 'error') {
          setLoading(false)
          es.close()
        } else if (msg.type === 'document') {
          setDocs(prev => [...prev, msg.data])
        }
      } catch (_) {}
    }

    es.onerror = () => {
      setLoading(false)
      es.close()
    }
  }

  useEffect(() => () => esRef.current?.close(), [])

  return (
    <div>
      {!started && (
        <button onClick={start} style={btnStyle}>
          Charger les statuts notariaux
        </button>
      )}

      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0' }}>
          <div style={spinner} />
          <span>Récupération en cours…</span>
        </div>
      )}

      {docs.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '12px 0' }}>
          {docs.map((doc, i) => (
            <li key={i} style={docItem}>
              <strong>{doc.date || doc.Date || '—'}</strong>{' '}
              <span style={{ color: '#555' }}>{doc.type || doc.Type || doc.titre || '—'}</span>
              {doc.url && (
                <a href={doc.url} target="_blank" rel="noreferrer" style={{ marginLeft: 8, fontSize: 12 }}>
                  [voir]
                </a>
              )}
            </li>
          ))}
        </ul>
      )}

      {started && !loading && docs.length === 0 && (
        <p style={{ color: '#888' }}>Aucun document trouvé.</p>
      )}
    </div>
  )
}

const btnStyle = {
  padding: '8px 18px', background: '#0969da', color: '#fff',
  border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 14,
}

const spinner = {
  width: 18, height: 18, borderRadius: '50%',
  border: '3px solid #ccc', borderTopColor: '#0969da',
  animation: 'spin 0.8s linear infinite',
}

const docItem = {
  padding: '8px 12px', borderLeft: '3px solid #0969da',
  marginBottom: 8, background: '#f6f8fa', borderRadius: '0 6px 6px 0',
}
