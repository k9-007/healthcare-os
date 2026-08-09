import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import i18n from '@/i18n'

type Theme = 'dark' | 'light'

interface AppState {
  uiLanguage: string
  telephonyMode: 'simulation' | 'plivo'
  theme: Theme
  setUiLanguage: (code: string) => void
  setTelephonyMode: (mode: 'simulation' | 'plivo') => void
  toggleTheme: () => void
}

function applyLanguage(code: string) {
  i18n.changeLanguage(code)
  document.documentElement.lang = code
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('light', theme === 'light')
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      uiLanguage: 'en-IN',
      telephonyMode: 'simulation',
      theme: 'dark',
      setUiLanguage: (uiLanguage) => {
        applyLanguage(uiLanguage)
        set({ uiLanguage })
      },
      setTelephonyMode: (telephonyMode) => set({ telephonyMode }),
      toggleTheme: () => {
        const theme: Theme = get().theme === 'dark' ? 'light' : 'dark'
        applyTheme(theme)
        set({ theme })
      },
    }),
    {
      name: 'healthcareos-ui',
      onRehydrateStorage: () => (state) => {
        if (!state) return
        // Migrate persisted Twilio selection → Plivo
        if ((state.telephonyMode as string) === 'twilio') {
          state.telephonyMode = 'plivo'
        }
        if (state.uiLanguage !== 'en-IN') applyLanguage(state.uiLanguage)
        applyTheme(state.theme)
      },
    },
  ),
)
