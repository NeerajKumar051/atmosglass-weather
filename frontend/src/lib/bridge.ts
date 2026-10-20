import { useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Types (mirror the normalized dictionaries produced by the Python backend)
// ---------------------------------------------------------------------------
export interface AppInfo {
  name: string
  version: string
  message: string
}

export interface WeatherCondition {
  code: number | null
  label: string
  icon: string
  scene: string
}

export interface LocationSuggestion {
  id: string
  name: string
  admin1: string | null
  country: string | null
  country_code: string | null
  latitude: number
  longitude: number
  timezone: string | null
  display_name: string
}

export interface CurrentWeather {
  time: string | null
  is_day: boolean | null
  temperature: number | null
  apparent_temperature: number | null
  humidity: number | null
  precipitation: number | null
  cloud_cover: number | null
  pressure: number | null
  wind_speed: number | null
  wind_direction: number | null
  wind_gusts: number | null
  visibility: number | null
  uv_index: number | null
  weather: WeatherCondition
}

export interface HourlyPoint {
  time: string
  temperature: number | null
  precipitation_probability: number | null
  is_day: boolean | null
  weather: WeatherCondition
}

export interface DailyPoint {
  date: string
  temperature_max: number | null
  temperature_min: number | null
  precipitation_probability_max: number | null
  weather: WeatherCondition
}

export interface WeatherUnits {
  temperature: string
  humidity: string
  precipitation: string
  pressure: string
  wind_speed: string
  visibility: string
  uv_index: string
}

export interface WeatherPayload {
  source: string
  fetched_at: string
  location: { name: string | null; latitude: number; longitude: number; elevation: number | null }
  timezone: { id: string | null; abbreviation: string | null; utc_offset_seconds: number | null }
  units: WeatherUnits
  current: CurrentWeather
  hourly: HourlyPoint[]
  daily: DailyPoint[]
  skipped_variables: string[]
}

export type BridgeEnvelope<T> =
  | { ok: true; data: T; meta: Record<string, unknown>; error: null }
  | { ok: false; data: null; meta: Record<string, unknown>; error: { code: string; message: string } }

export interface PywebviewApi {
  get_app_info: () => Promise<AppInfo>
  search_locations: (query: string) => Promise<BridgeEnvelope<LocationSuggestion[]>>
  get_weather: (location: {
    latitude: number
    longitude: number
    name: string
    display_name: string
  }) => Promise<BridgeEnvelope<WeatherPayload>>
}

// ---------------------------------------------------------------------------
// Phase 1B status hook (unchanged behaviour)
// ---------------------------------------------------------------------------
export type BridgeState =
  | { status: 'connecting' }
  | { status: 'connected'; info: AppInfo }
  | { status: 'error'; message: string }

const READY_TIMEOUT_MS = 5000

function isAppInfo(value: unknown): value is AppInfo {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  return (
    typeof v.name === 'string' &&
    typeof v.version === 'string' &&
    typeof v.message === 'string'
  )
}

/** Waits for pywebview, calls get_app_info(), and reports a readable state. */
export function useBridge(): BridgeState {
  const [state, setState] = useState<BridgeState>({ status: 'connecting' })

  useEffect(() => {
    let cancelled = false
    let started = false
    let timer: number | undefined

    const update = (next: BridgeState) => {
      if (!cancelled) setState(next)
    }

    const load = async () => {
      if (started) return
      started = true
      window.clearTimeout(timer)
      try {
        const api = window.pywebview?.api
        if (!api || typeof api.get_app_info !== 'function') {
          throw new Error('Python API is not available.')
        }
        const info: unknown = await api.get_app_info()
        if (!isAppInfo(info)) {
          throw new Error('Python returned an unexpected response.')
        }
        update({ status: 'connected', info })
      } catch (err) {
        update({
          status: 'error',
          message: err instanceof Error ? err.message : String(err),
        })
      }
    }

    if (window.pywebview?.api?.get_app_info) {
      // Bridge was already ready before React mounted.
      void load()
    } else {
      window.addEventListener('pywebviewready', load, { once: true })
      timer = window.setTimeout(() => {
        update({
          status: 'error',
          message:
            'pywebview was not detected. Start the app with main.py instead of a browser.',
        })
      }, READY_TIMEOUT_MS)
    }

    return () => {
      cancelled = true
      window.clearTimeout(timer)
      window.removeEventListener('pywebviewready', load)
    }
  }, [])

  return state
}

// ---------------------------------------------------------------------------
// Typed weather calls (all network access happens in Python)
// ---------------------------------------------------------------------------
export class BridgeError extends Error {
  code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'BridgeError'
    this.code = code
  }
}

/** Resolves once window.pywebview.api exists (waits for `pywebviewready`). */
export function whenBridgeReady(timeoutMs = 8000): Promise<PywebviewApi> {
  return new Promise((resolve, reject) => {
    const existing = window.pywebview?.api
    if (existing?.search_locations) {
      resolve(existing)
      return
    }
    const onReady = () => {
      window.clearTimeout(timer)
      const api = window.pywebview?.api
      if (api) resolve(api)
      else reject(new Error('The Python API is not available.'))
    }
    const timer = window.setTimeout(() => {
      window.removeEventListener('pywebviewready', onReady)
      reject(new Error('pywebview was not detected. Start the app with main.py instead of a browser.'))
    }, timeoutMs)
    window.addEventListener('pywebviewready', onReady, { once: true })
  })
}

async function invoke<T>(call: (api: PywebviewApi) => Promise<BridgeEnvelope<T>>): Promise<T> {
  let envelope: BridgeEnvelope<T>
  try {
    envelope = await call(await whenBridgeReady())
  } catch (err) {
    throw new BridgeError('BRIDGE_ERROR', err instanceof Error ? err.message : 'The Python bridge call failed.')
  }
  if (!envelope || typeof envelope !== 'object' || typeof envelope.ok !== 'boolean') {
    throw new BridgeError('INVALID_RESPONSE', 'Python returned an unexpected response.')
  }
  if (!envelope.ok) {
    throw new BridgeError(envelope.error?.code ?? 'UNKNOWN_ERROR', envelope.error?.message ?? 'Something went wrong.')
  }
  return envelope.data
}

export async function searchLocations(query: string): Promise<LocationSuggestion[]> {
  const data = await invoke((api) => api.search_locations(query))
  if (!Array.isArray(data)) {
    throw new BridgeError('INVALID_RESPONSE', 'Location search returned unexpected data.')
  }
  return data
}

export async function getWeather(location: LocationSuggestion): Promise<WeatherPayload> {
  const data = await invoke((api) =>
    api.get_weather({
      latitude: location.latitude,
      longitude: location.longitude,
      name: location.name,
      display_name: location.display_name,
    }),
  )
  if (!data || !data.current || !Array.isArray(data.hourly) || !Array.isArray(data.daily)) {
    throw new BridgeError('INVALID_RESPONSE', 'The forecast arrived in an unexpected format.')
  }
  return data
}