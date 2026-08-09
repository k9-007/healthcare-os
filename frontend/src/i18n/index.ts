import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en'
import hi from './locales/hi'
import ta from './locales/ta'
import kn from './locales/kn'
import mr from './locales/mr'

// Languages without a table (te, bn, gu, pa, ml) fall back to English.
i18n.use(initReactI18next).init({
  resources: {
    'en-IN': { translation: en },
    'hi-IN': { translation: hi },
    'ta-IN': { translation: ta },
    'kn-IN': { translation: kn },
    'mr-IN': { translation: mr },
  },
  lng: 'en-IN',
  fallbackLng: 'en-IN',
  interpolation: { escapeValue: false },
})

export default i18n
