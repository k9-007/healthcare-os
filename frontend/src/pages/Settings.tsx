import { useTranslation } from 'react-i18next'
import { useAppStore } from '@/store/useAppStore'
import { cx, Field, Input, SectionHeader } from '@/components/ui/primitives'

export function Settings() {
  const { t } = useTranslation()
  const { telephonyMode, setTelephonyMode } = useAppStore()

  return (
    <div className="max-w-2xl space-y-5">
      <section className="panel p-5">
        <SectionHeader title={t('settings.telephony')} />
        <p className="mb-4 text-xs leading-relaxed text-mist">{t('settings.telephonyDesc')}</p>
        <div className="flex gap-2">
          {(['simulation', 'twilio'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setTelephonyMode(mode)}
              className={cx(
                'flex-1 rounded-md border px-4 py-3 text-left transition-colors focus-ring cursor-pointer',
                telephonyMode === mode ? 'border-accent/50 bg-accent/6' : 'border-line hover:border-line-strong',
              )}
            >
              <div className={cx('text-[13px] font-medium', telephonyMode === mode ? 'text-accent' : 'text-fog')}>
                {mode === 'simulation' ? t('settings.simulation') : t('settings.twilio')}
              </div>
              <div className="mt-0.5 text-[11px] text-mist">
                {mode === 'simulation' ? t('settings.simulationDesc') : t('settings.twilioDesc')}
              </div>
            </button>
          ))}
        </div>
        {telephonyMode === 'twilio' && (
          <div className="mt-4 grid grid-cols-2 gap-3">
            <Field label="Account SID"><Input placeholder="ACxxxxxxxx" /></Field>
            <Field label="Auth token"><Input type="password" placeholder="••••••••" /></Field>
            <Field label="Voice number"><Input placeholder="+1 555 …" /></Field>
            <Field label="Public base URL" hint="ngrok or hosted URL for webhooks">
              <Input placeholder="https://…" />
            </Field>
          </div>
        )}
      </section>

      <section className="panel p-5">
        <SectionHeader title={t('settings.scheduler')} />
        <div className="grid grid-cols-2 gap-3">
          <Field label="Tick interval (seconds)" hint="Lower for demos so calls fire fast.">
            <Input type="number" defaultValue={60} min={5} />
          </Field>
          <Field label="Default call window">
            <Input defaultValue="08:00-20:00" />
          </Field>
          <Field label="Max retries per slot">
            <Input type="number" defaultValue={3} min={0} max={5} />
          </Field>
          <Field label="Retry backoff (minutes, csv)">
            <Input defaultValue="15,60,240" />
          </Field>
        </div>
      </section>

      <section className="panel p-5">
        <SectionHeader title={t('settings.sarvam')} />
        <div className="grid grid-cols-2 gap-3">
          <Field label="API subscription key">
            <Input type="password" placeholder="••••••••••••" />
          </Field>
          <Field label="TTS voice">
            <Input defaultValue="priya · warm female" />
          </Field>
        </div>
        <p className="mt-3 text-[11px] text-faint">
          Keys are stored server-side only. These fields configure the FastAPI backend once it's connected.
        </p>
      </section>
    </div>
  )
}
