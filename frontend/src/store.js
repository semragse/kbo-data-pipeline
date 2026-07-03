import { configureStore, createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import axios from 'axios'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// ── Search slice ───────────────────────────────────────────────────────────────

export const searchEntreprises = createAsyncThunk(
  'search/query',
  async (q) => {
    const { data } = await axios.get(`${API}/api/search`, { params: { q, limit: 20 } })
    return data.results
  }
)

const searchSlice = createSlice({
  name: 'search',
  initialState: { results: [], loading: false, error: null, query: '' },
  reducers: {
    setQuery: (state, action) => { state.query = action.payload },
    clearResults: (state) => { state.results = []; state.query = '' },
  },
  extraReducers: (builder) => {
    builder
      .addCase(searchEntreprises.pending,   (state) => { state.loading = true; state.error = null })
      .addCase(searchEntreprises.fulfilled, (state, action) => { state.loading = false; state.results = action.payload })
      .addCase(searchEntreprises.rejected,  (state, action) => { state.loading = false; state.error = action.error.message })
  },
})

// ── Entreprise slice ───────────────────────────────────────────────────────────

export const fetchEntreprise = createAsyncThunk(
  'entreprise/fetch',
  async (num) => {
    const { data } = await axios.get(`${API}/api/entreprise/${num}`)
    return data
  }
)

const entrepriseSlice = createSlice({
  name: 'entreprise',
  initialState: { current: null, loading: false, error: null },
  reducers: {
    clearEntreprise: (state) => { state.current = null },
  },
  extraReducers: (builder) => {
    builder
      .addCase(fetchEntreprise.pending,   (state) => { state.loading = true; state.error = null })
      .addCase(fetchEntreprise.fulfilled, (state, action) => { state.loading = false; state.current = action.payload })
      .addCase(fetchEntreprise.rejected,  (state, action) => { state.loading = false; state.error = action.error.message })
  },
})

export const { setQuery, clearResults } = searchSlice.actions
export const { clearEntreprise }        = entrepriseSlice.actions

export const store = configureStore({
  reducer: {
    search:     searchSlice.reducer,
    entreprise: entrepriseSlice.reducer,
  },
})
