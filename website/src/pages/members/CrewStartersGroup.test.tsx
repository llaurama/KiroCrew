import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { MemberRosterRow } from '../../api/client'

vi.mock('../../api/client', () => ({
  api: { updateKirocrewAgent: vi.fn().mockResolvedValue({ ok: true }) },
}))

import { api } from '../../api/client'
import CrewStartersGroup from './CrewStartersGroup'

const update = api.updateKirocrewAgent as ReturnType<typeof vi.fn>

function renderGroup(starters?: string[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const member = { name: 'radar', starters } as unknown as MemberRosterRow
  return render(
    <QueryClientProvider client={qc}>
      <CrewStartersGroup member={member} name="Radar" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  update.mockResolvedValue({ ok: true })
})

describe('the Bot profile starters box', () => {
  it('shows the saved starters one per line and keeps Save off until something changes', () => {
    renderGroup(['Plan my week', 'Summarize my inbox'])
    expect(screen.getByTestId('crew-profile-starters-input')).toHaveValue('Plan my week\nSummarize my inbox')
    expect(screen.getByTestId('crew-profile-starters-save')).toBeDisabled()
  })

  it('saves the non-blank lines, trimmed, as the starters list', async () => {
    renderGroup([])
    fireEvent.change(screen.getByTestId('crew-profile-starters-input'), { target: { value: '  Plan my week \n\n Summarize  ' } })
    fireEvent.click(screen.getByTestId('crew-profile-starters-save'))
    await waitFor(() => expect(update).toHaveBeenCalledWith('radar', { starters: ['Plan my week', 'Summarize'] }))
  })

  it('says some lines were left out when the gateway returns a warning, and still saved', async () => {
    update.mockResolvedValue({ ok: true, warnings: ['starter is too long and was left out'] })
    renderGroup([])
    fireEvent.change(screen.getByTestId('crew-profile-starters-input'), { target: { value: 'ok' } })
    fireEvent.click(screen.getByTestId('crew-profile-starters-save'))
    expect(await screen.findByTestId('crew-profile-starters-left-out')).toBeInTheDocument()
  })

  it('reports a failed save', async () => {
    update.mockRejectedValue(new Error('boom'))
    renderGroup([])
    fireEvent.change(screen.getByTestId('crew-profile-starters-input'), { target: { value: 'ok' } })
    fireEvent.click(screen.getByTestId('crew-profile-starters-save'))
    expect(await screen.findByTestId('crew-profile-starters-error')).toBeInTheDocument()
  })
})
