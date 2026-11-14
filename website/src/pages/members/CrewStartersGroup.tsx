import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, type MemberRosterRow } from '../../api/client'
import { MEMBERS_ROSTER_QUERY_KEY } from '../../api/membersQuery'
import { Btn } from '../../components/ui'
import ErrorNotice from '../../components/ErrorNotice'
import { RowGroup } from './profileRow'

/** The gateway's bounds (`member_starters.py`); it enforces them, this only words the hint. */
const STARTER_MAX_COUNT = 4
const STARTER_MAX_CHARS = 120

/**
 * The Bot's conversation starters, one per line: the prompts its empty chat offers as
 * buttons. The gateway keeps what passes its bounds and names what it left out, so a
 * line that is too long or repeated never blocks the rest from saving; the box then
 * shows what was kept.
 */
export default function CrewStartersGroup({ member, name }: { member: MemberRosterRow; name: string }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const savedText = (member.starters ?? []).join('\n')
  const [text, setText] = useState(savedText)
  const [leftOut, setLeftOut] = useState(false)
  // A change from elsewhere (another window, an applied card) reaches the box; a read
  // that returns the same starters leaves what the owner is typing alone.
  useEffect(() => { setText(savedText) }, [savedText])

  const save = useMutation({
    mutationFn: (starters: string[]) => api.updateKirocrewAgent(member.name, { starters }) as Promise<{ warnings?: string[] }>,
    onSuccess: (result) => { setLeftOut((result?.warnings?.length ?? 0) > 0) },
    onSettled: () => queryClient.invalidateQueries({ queryKey: MEMBERS_ROSTER_QUERY_KEY }),
  })
  const lines = text.split('\n').map((line) => line.trim()).filter(Boolean)
  const dirty = lines.join('\n') !== savedText

  return (
    <>
      <RowGroup caption={t('pages.membersPage.profile_starters_hint', { name, max: STARTER_MAX_COUNT, chars: STARTER_MAX_CHARS })}>
        <div className="flex flex-col gap-2 px-3.5 py-3" data-testid="crew-profile-starters">
          <label className="text-[13px] font-semibold text-text" htmlFor="crew-profile-starters-input">
            {t('pages.membersPage.profile_starters_title')}
          </label>
          <textarea
            id="crew-profile-starters-input"
            aria-label={t('pages.membersPage.profile_starters_title')}
            value={text}
            rows={4}
            disabled={save.isPending}
            onChange={(e) => { setText(e.target.value); setLeftOut(false) }}
            className="w-full rounded-lg border border-border bg-bg px-2.5 py-2 text-[13px] text-text focus-ring"
            data-testid="crew-profile-starters-input"
          />
          <div className="flex justify-end">
            <Btn
              type="button"
              primary
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate(lines)}
              data-testid="crew-profile-starters-save"
            >
              {t('pages.membersPage.profile_starters_save')}
            </Btn>
          </div>
        </div>
      </RowGroup>
      <ErrorNotice message={save.isError ? t('pages.membersPage.profile_starters_failed') : null} testId="crew-profile-starters-error" />
      {leftOut && (
        <p className="px-3.5 text-[12px] leading-snug text-muted" role="status" data-testid="crew-profile-starters-left-out">
          {t('pages.membersPage.profile_starters_left_out', { max: STARTER_MAX_COUNT, chars: STARTER_MAX_CHARS })}
        </p>
      )}
    </>
  )
}
