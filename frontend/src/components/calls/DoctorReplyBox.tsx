import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Send } from 'lucide-react'
import type { Patient } from '@/api/types'
import { useDoctorReply } from '@/api/hooks'
import { languageLabel } from '@/lib/languages'
import { Button, SectionHeader } from '@/components/ui/primitives'

export function DoctorReplyBox({ patient }: { patient: Patient }) {
  const { t } = useTranslation()
  const [message, setMessage] = useState('')
  const [sent, setSent] = useState(false)
  const reply = useDoctorReply(patient.id)

  const language = languageLabel(patient.preferredLanguage)

  async function onSend() {
    if (message.trim().length < 4) return
    await reply.mutateAsync(message.trim())
    setMessage('')
    setSent(true)
    setTimeout(() => setSent(false), 5000)
  }

  return (
    <section className="panel p-4">
      <SectionHeader title={t('calls.replyTitle')} />
      <textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={2}
        placeholder={t('calls.replyPlaceholder', { language })}
        className="w-full resize-none rounded-md border border-line-strong bg-canvas p-2.5 text-[13px] text-bright placeholder:text-faint focus-ring"
      />
      <div className="mt-2.5 flex items-center justify-between">
        <span className="text-[11px] text-mist">
          {sent
            ? t('calls.replySent', { name: patient.name.split(' ')[0], language })
            : t('calls.replyIdle')}
        </span>
        <Button variant="primary" size="sm" onClick={onSend} disabled={reply.isPending || message.trim().length < 4}>
          <Send size={12} /> {reply.isPending ? t('calls.sending') : t('calls.send')}
        </Button>
      </div>
    </section>
  )
}
