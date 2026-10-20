import type { PywebviewApi } from './lib/bridge'

declare global {
  interface Window {
    pywebview?: {
      api?: PywebviewApi
    }
  }

  interface WindowEventMap {
    pywebviewready: Event
  }
}