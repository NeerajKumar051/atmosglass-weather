import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import {
  BridgeError,
  getWeather,
  searchLocations,
  useBridge,
  type DailyPoint,
  type HourlyPoint,
  type LocationSuggestion,
  type WeatherPayload,
  type WeatherUnits,
} from './lib/bridge'
import './styles/dashboard.css'

const DEMO_CITY = 'Jaipur' // resolved through geocoding; no coordinates are hardcoded
const MIN_QUERY = 2
const SEARCH_DEBOUNCE_MS = 450

type Phase = 'idle' | 'loading' | 'ready' | 'error'
interface UiError {
  code: string
  message: string
}
interface SearchState {
  phase: Phase
  query: string
  results: LocationSuggestion[]
  error: UiError | null
}
const EMPTY_SEARCH: SearchState = { phase: 'idle', query: '', results: [], error: null }

function toUiError(err: unknown): UiError {
  if (err instanceof BridgeError) return { code: err.code, message: err.message }
  return { code: 'UNKNOWN_ERROR', message: 'Something went wrong. Please try again.' }
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
const ICONS: Record<string, string> = {
  sun: '☀️', moon: '🌙', 'sun-cloud': '🌤️', 'moon-cloud': '☁️', cloud: '☁️', fog: '🌫️',
  drizzle: '🌦️', rain: '🌧️', 'freezing-rain': '🌨️', snow: '❄️', thunderstorm: '⛈️', unknown: '🌡️',
}
const iconFor = (icon: string) => ICONS[icon] ?? ICONS.unknown

const DASH = '—'
const round = (v: number | null | undefined) => (v == null ? DASH : String(Math.round(v)))
const temp = (v: number | null | undefined) => (v == null ? DASH : `${Math.round(v)}°`)
const withUnit = (v: number | null | undefined, unit: string, digits = 0) =>
  v == null ? DASH : `${digits ? v.toFixed(digits) : Math.round(v)} ${unit}`.trim()

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
function compass(deg: number | null): string {
  if (deg == null) return ''
  return COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]
}

function uvLabel(uv: number | null): string {
  if (uv == null) return ''
  if (uv < 3) return 'Low'
  if (uv < 6) return 'Moderate'
  if (uv < 8) return 'High'
  if (uv < 11) return 'Very high'
  return 'Extreme'
}

function visibilityText(v: number | null, unit: string): string {
  if (v == null) return DASH
  if (unit === 'm') return v >= 1000 ? `${(v / 1000).toFixed(1)} km` : `${Math.round(v)} m`
  return `${Math.round(v)} ${unit}`
}

/** The next 24 hourly points, starting at the current hour in the city's own timezone. */
function next24Hours(weather: WeatherPayload): HourlyPoint[] {
  const now = weather.current.time
  const key = now ? `${now.slice(0, 13)}:00` : ''
  const found = key ? weather.hourly.findIndex((h) => h.time >= key) : 0
  const start = found < 0 ? 0 : found
  return weather.hourly.slice(start, start + 24)
}

function parseDate(date: string): Date | null {
  const [y, m, d] = date.split('-').map(Number)
  return y && m && d ? new Date(y, m - 1, d) : null
}
function dayLabel(date: string, index: number): string {
  if (index === 0) return 'Today'
  return parseDate(date)?.toLocaleDateString(undefined, { weekday: 'short' }) ?? date
}
function shortDate(date: string): string {
  return parseDate(date)?.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) ?? ''
}
function clockTime(iso: string | null): string {
  if (!iso) return DASH
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? DASH : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
const subtitleOf = (loc: LocationSuggestion) => [loc.admin1, loc.country].filter(Boolean).join(', ')

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
export default function App() {
  const bridge = useBridge()

  const [query, setQuery] = useState('')
  const [showResults, setShowResults] = useState(false)
  const [search, setSearch] = useState<SearchState>(EMPTY_SEARCH)

  const [selected, setSelected] = useState<LocationSuggestion | null>(null)
  const [weather, setWeather] = useState<{ data: WeatherPayload; forId: string } | null>(null)
  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState<UiError | null>(null)

  const searchSeq = useRef(0)
  const weatherSeq = useRef(0)
  const debounce = useRef<number | undefined>(undefined)
  const searchBox = useRef<HTMLDivElement>(null)

  const loadWeather = useCallback(async (location: LocationSuggestion) => {
    const id = ++weatherSeq.current // ignore stale responses
    setPhase('loading')
    setError(null)
    try {
      const data = await getWeather(location)
      if (id !== weatherSeq.current) return
      setWeather({ data, forId: location.id })
      setPhase('ready')
    } catch (err) {
      if (id !== weatherSeq.current) return
      setError(toUiError(err))
      setPhase('error')
    }
  }, [])

  const startDemo = useCallback(async () => {
    const id = ++weatherSeq.current
    setPhase('loading')
    setError(null)
    try {
      const results = await searchLocations(DEMO_CITY)
      if (id !== weatherSeq.current) return
      // Only auto-select an exact name match; otherwise let the user choose.
      const match = results.find((r) => r.name.toLowerCase() === DEMO_CITY.toLowerCase())
      if (match) {
        setSelected(match)
        void loadWeather(match)
      } else {
        setSearch({ phase: 'ready', query: DEMO_CITY, results, error: null })
        setQuery(DEMO_CITY)
        setShowResults(true)
        setPhase('idle')
      }
    } catch (err) {
      if (id !== weatherSeq.current) return
      setError(toUiError(err))
      setPhase('error')
    }
  }, [loadWeather])

  useEffect(() => {
    void startDemo()
  }, [startDemo])

  const runSearch = useCallback(async (raw: string) => {
    const q = raw.trim()
    const id = ++searchSeq.current
    setSearch({ phase: 'loading', query: q, results: [], error: null })
    try {
      const results = await searchLocations(q)
      if (id === searchSeq.current) setSearch({ phase: 'ready', query: q, results, error: null })
    } catch (err) {
      if (id === searchSeq.current) setSearch({ phase: 'error', query: q, results: [], error: toUiError(err) })
    }
  }, [])

  // Debounced live suggestions while typing.
  useEffect(() => {
    const q = query.trim()
    if (q.length < MIN_QUERY) return undefined
    debounce.current = window.setTimeout(() => void runSearch(q), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(debounce.current)
  }, [query, runSearch])

  // Close the suggestion list when clicking elsewhere.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (searchBox.current && !searchBox.current.contains(e.target as Node)) setShowResults(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const onQueryChange = (value: string) => {
    setQuery(value)
    setShowResults(true)
    if (value.trim().length < MIN_QUERY) {
      searchSeq.current++
      setSearch(EMPTY_SEARCH)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    window.clearTimeout(debounce.current)
    setShowResults(true)
    if (query.trim().length < MIN_QUERY) {
      setSearch({ phase: 'error', query: query.trim(), results: [], error: { code: 'VALIDATION_ERROR', message: 'Type at least 2 characters.' } })
      return
    }
    void runSearch(query) // shows suggestions; never auto-selects
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') setShowResults(false)
  }

  const choose = (loc: LocationSuggestion) => {
    setSelected(loc)
    setQuery('')
    setShowResults(false)
    setSearch(EMPTY_SEARCH)
    void loadWeather(loc)
  }

  const retry = () => {
    if (selected) void loadWeather(selected)
    else void startDemo()
  }

  // Only show data that belongs to the selected city.
  const data = weather && selected && weather.forId === selected.id ? weather.data : null
  const busy = phase === 'loading'
  const scene = data?.current.weather.scene ?? 'neutral'
  const night = data?.current.is_day === false

  const trimmed = query.trim()
  const panelOpen = showResults && (trimmed.length >= MIN_QUERY || search.phase === 'error')

  return (
    <div className="ag-app" data-scene={scene} data-night={night}>
      <div className="ag-wrap">
        <header className="ag-header">
          <div className="ag-brand">
            <h1>AtmosGlass Weather</h1>
            <p>Live conditions, forecast and atmosphere.</p>
          </div>

          <div className="ag-search" ref={searchBox}>
            <form onSubmit={onSubmit} role="search">
              <label className="ag-sr" htmlFor="city-search">Search for a city</label>
              <input
                id="city-search"
                type="search"
                value={query}
                maxLength={100}
                placeholder="Search a city, e.g. Tokyo"
                autoComplete="off"
                onChange={(e) => onQueryChange(e.target.value)}
                onFocus={() => setShowResults(true)}
                onKeyDown={onKeyDown}
              />
              <button type="submit" className="ag-btn">Search</button>
            </form>

            {panelOpen && (
              <div className="ag-suggest" role="region" aria-label="Location suggestions">
                {search.phase === 'error' && search.error ? (
                  <div className="ag-suggest-msg">
                    <span>{search.error.message}</span>
                    {search.error.code !== 'VALIDATION_ERROR' && (
                      <button type="button" className="ag-btn ag-btn-small" onClick={() => void runSearch(trimmed)}>Retry search</button>
                    )}
                  </div>
                ) : search.phase === 'loading' || search.query !== trimmed ? (
                  <div className="ag-suggest-msg" role="status"><span className="ag-spinner" aria-hidden="true" /> Searching…</div>
                ) : search.results.length === 0 ? (
                  <div className="ag-suggest-msg" role="status">
                    No cities found for “{search.query}”. Check the spelling or try a nearby city.
                  </div>
                ) : (
                  <ul>
                    {search.results.map((loc) => (
                      <li key={loc.id}>
                        <button type="button" onClick={() => choose(loc)}>
                          <strong>{loc.name}</strong>
                          <span>{subtitleOf(loc) || 'Unknown region'}</span>
                          <small>{loc.latitude.toFixed(2)}°, {loc.longitude.toFixed(2)}°</small>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </header>

        <main>
          {!selected && phase === 'idle' && (
            <section className="ag-card ag-message">
              <h2>Pick a city</h2>
              <p>Choose one of the suggestions above, or search for another city.</p>
            </section>
          )}

          {phase === 'error' && !data && error && (
            <section className="ag-card ag-message" role="alert">
              <h2>Weather unavailable</h2>
              <p>{error.message}</p>
              <button type="button" className="ag-btn" onClick={retry}>Try again</button>
            </section>
          )}

          {busy && !data && <Skeleton />}

          {data && selected && (
            <>
              {phase === 'error' && error && (
                <div className="ag-banner" role="alert">
                  <span>Couldn’t refresh: {error.message} Showing data from {clockTime(data.fetched_at)}.</span>
                  <button type="button" className="ag-btn ag-btn-small" onClick={retry}>Retry</button>
                </div>
              )}
              <Dashboard
                key={selected.id}
                location={selected}
                data={data}
                refreshing={busy}
                onRefresh={() => void loadWeather(selected)}
              />
            </>
          )}
        </main>

        <footer className="ag-footer">
          <span>Weather data by Open-Meteo.com (open-meteo.com), licensed CC BY 4.0.</span>
          <span className="ag-bridge" role="status">
            <i className={`ag-dot ag-dot-${bridge.status}`} aria-hidden="true" />
            {bridge.status === 'connected' && `Python bridge connected (v${bridge.info.version})`}
            {bridge.status === 'connecting' && 'Connecting to Python…'}
            {bridge.status === 'error' && 'Python bridge unavailable'}
          </span>
        </footer>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------
function Skeleton() {
  return (
    <div className="ag-skeleton" role="status" aria-label="Loading weather">
      <div className="ag-card ag-sk-block ag-sk-hero" />
      <div className="ag-card ag-sk-block" />
      <div className="ag-card ag-sk-block" />
    </div>
  )
}

interface DashboardProps {
  location: LocationSuggestion
  data: WeatherPayload
  refreshing: boolean
  onRefresh: () => void
}

function Dashboard({ location, data, refreshing, onRefresh }: DashboardProps) {
  const { current, units } = data
  const hours = useMemo(() => next24Hours(data), [data])
  const unitLetter = units.temperature.replace('°', '')

  return (
    <div className="ag-dash">
      <section className="ag-card ag-hero" aria-labelledby="city-name">
        <div className="ag-hero-top">
          <div>
            <h2 id="city-name">{location.name}</h2>
            <p className="ag-sub">{subtitleOf(location) || location.display_name}</p>
            <p className="ag-muted">
              Local time {current.time ? current.time.slice(11, 16) : DASH}
              {data.timezone.id ? ` · ${data.timezone.id}` : ''}
            </p>
          </div>
          <div className="ag-refresh">
            <button type="button" className="ag-btn" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? <span className="ag-spinner" aria-hidden="true" /> : '↻'} {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
            <small className="ag-muted">Updated {clockTime(data.fetched_at)}</small>
          </div>
        </div>

        <div className="ag-hero-main">
          <span className="ag-hero-icon" aria-hidden="true">{iconFor(current.weather.icon)}</span>
          <div>
            <div className="ag-temp">
              {temp(current.temperature)}<span className="ag-temp-unit">{unitLetter}</span>
            </div>
            <div className="ag-condition">{current.weather.label}</div>
          </div>
        </div>

        <dl className="ag-chips">
          <div><dt>Feels like</dt><dd>{temp(current.apparent_temperature)}</dd></div>
          <div><dt>Humidity</dt><dd>{current.humidity == null ? DASH : `${round(current.humidity)}%`}</dd></div>
          <div>
            <dt>Wind</dt>
            <dd>{withUnit(current.wind_speed, units.wind_speed)} {compass(current.wind_direction)}</dd>
          </div>
        </dl>
      </section>

      <Metrics data={data} />

      <section className="ag-card" aria-labelledby="hourly-title">
        <h3 id="hourly-title">Next 24 hours</h3>
        {hours.length === 0 ? (
          <p className="ag-muted">Hourly forecast is not available.</p>
        ) : (
          <ol className="ag-hourly">
            {hours.map((h, i) => (
              <HourItem key={h.time} hour={h} first={i === 0} />
            ))}
          </ol>
        )}
      </section>

      <section className="ag-card" aria-labelledby="daily-title">
        <h3 id="daily-title">7-day forecast</h3>
        <DailyList days={data.daily.slice(0, 7)} units={units} />
      </section>
    </div>
  )
}

function Metrics({ data }: { data: WeatherPayload }) {
  const { current: c, units } = data
  const tiles: { label: string; value: string; note?: string }[] = [
    { label: 'Precipitation', value: withUnit(c.precipitation, units.precipitation, 1) },
    { label: 'Pressure', value: withUnit(c.pressure, units.pressure) },
    { label: 'Visibility', value: visibilityText(c.visibility, units.visibility) },
    { label: 'UV index', value: c.uv_index == null ? DASH : c.uv_index.toFixed(1), note: uvLabel(c.uv_index) },
    { label: 'Cloud cover', value: c.cloud_cover == null ? DASH : `${round(c.cloud_cover)}%` },
    { label: 'Wind gusts', value: withUnit(c.wind_gusts, units.wind_speed) },
  ]
  return (
    <section className="ag-metrics" aria-label="Additional conditions">
      {tiles.map((t) => (
        <div className="ag-card ag-tile" key={t.label}>
          <span className="ag-tile-label">{t.label}</span>
          <span className="ag-tile-value">{t.value}</span>
          {t.note && <span className="ag-tile-note">{t.note}</span>}
        </div>
      ))}
    </section>
  )
}

function HourItem({ hour, first }: { hour: HourlyPoint; first: boolean }) {
  const p = hour.precipitation_probability
  return (
    <li className="ag-hour">
      <span className="ag-hour-time">{first ? 'Now' : hour.time.slice(11, 16)}</span>
      <span className="ag-hour-icon" role="img" aria-label={hour.weather.label} title={hour.weather.label}>
        {iconFor(hour.weather.icon)}
      </span>
      <span className="ag-hour-temp">{temp(hour.temperature)}</span>
      <span className="ag-hour-rain">{p == null ? '' : `${Math.round(p)}%`}</span>
    </li>
  )
}

function DailyList({ days, units }: { days: DailyPoint[]; units: WeatherUnits }) {
  const mins = days.map((d) => d.temperature_min).filter((v): v is number => v != null)
  const maxs = days.map((d) => d.temperature_max).filter((v): v is number => v != null)
  const lo = mins.length ? Math.min(...mins) : 0
  const hi = maxs.length ? Math.max(...maxs) : 0
  const span = hi - lo || 1

  if (days.length === 0) return <p className="ag-muted">Daily forecast is not available.</p>
  return (
    <ul className="ag-daily">
      {days.map((d, i) => {
        const left = d.temperature_min == null ? 0 : ((d.temperature_min - lo) / span) * 100
        const right = d.temperature_max == null ? 100 : ((d.temperature_max - lo) / span) * 100
        const rain = d.precipitation_probability_max
        return (
          <li className="ag-day" key={d.date}>
            <span className="ag-day-name">
              <strong>{dayLabel(d.date, i)}</strong>
              <small>{shortDate(d.date)}</small>
            </span>
            <span className="ag-day-cond">
              <span aria-hidden="true">{iconFor(d.weather.icon)}</span> {d.weather.label}
              {rain != null && rain > 0 && <small> · {Math.round(rain)}% rain</small>}
            </span>
            <span className="ag-day-range" aria-label={`Low ${temp(d.temperature_min)}, high ${temp(d.temperature_max)} ${units.temperature}`}>
              <span className="ag-day-min">{temp(d.temperature_min)}</span>
              <span className="ag-bar" aria-hidden="true">
                <i style={{ left: `${left}%`, width: `${Math.max(right - left, 6)}%` }} />
              </span>
              <span className="ag-day-max">{temp(d.temperature_max)}</span>
            </span>
          </li>
        )
      })}
    </ul>
  )
}