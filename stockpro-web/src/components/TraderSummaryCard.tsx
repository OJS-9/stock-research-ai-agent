import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useApiClient } from '../api/client'
import Icon from './Icon'

interface PublicViewData {
  symbol: string
  status: 'ready' | 'computing' | 'error'
  summary_md: string | null
  bullish_pct: number | null
  top_themes: string[]
  last_updated: string | null
  error_message: string | null
}

interface SectionRef {
  id: string
  title: string
}

// Pull the body text that follows a given heading in the markdown report,
// stopping at the next heading. Returns '' when the section is not present.
function extractSection(reportText: string, headings: string[]): string {
  if (!reportText) return ''
  const lines = reportText.split('\n')
  const wanted = headings.map(h => h.toLowerCase())
  let capturing = false
  const collected: string[] = []
  for (const line of lines) {
    const headingMatch = line.match(/^#{1,3}\s+(.+)$/)
    if (headingMatch) {
      if (capturing) break
      const title = headingMatch[1].trim().toLowerCase()
      if (wanted.some(w => title.includes(w))) {
        capturing = true
      }
      continue
    }
    if (capturing && line.trim()) collected.push(line.trim())
  }
  return collected.join(' ').trim()
}

// Strip light markdown so the snippet reads as plain text in the compact panel.
function stripMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/^[-*]\s+/gm, '')
    .trim()
}

// First `count` sentences of a block of text.
function firstSentences(text: string, count: number): string {
  const clean = stripMarkdown(text)
  if (!clean) return ''
  const sentences = clean.match(/[^.!?]+[.!?]+/g)
  if (!sentences) return clean
  return sentences.slice(0, count).join(' ').replace(/\s+/g, ' ').trim()
}

const panelStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  background: '#0c0a09',
  border: '1px solid #292524',
  borderRadius: 12,
  padding: '14px 16px',
}

const panelLabelStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.07em',
  color: '#a8a29e',
  marginBottom: 8,
}

export default function TraderSummaryCard({
  reportText,
  symbol,
  sections,
  isMobile,
}: {
  reportText: string
  symbol: string
  sections: SectionRef[]
  isMobile: boolean
}) {
  const api = useApiClient()
  // Desktop expanded by default; mobile collapsed by default.
  const [expanded, setExpanded] = useState(!isMobile)

  // Fetch once on mount. No refetchInterval: if the public view is not ready,
  // the Crowd Signal panel simply does not render.
  const { data } = useQuery<PublicViewData>({
    queryKey: ['public-view-summary', symbol],
    queryFn: async () => {
      const res = await api.get(`/api/ticker/${symbol}/public-view`)
      if (!res.ok) throw new Error('Failed to load public view')
      return res.json()
    },
    enabled: !!symbol && symbol !== '?',
  })

  const business = firstSentences(
    extractSection(reportText, ['executive summary', 'business overview']),
    3
  )
  const catalyst = firstSentences(
    extractSection(reportText, ['growth catalysts', 'growth catalyst', 'catalysts']),
    1
  )

  const crowdReady = data?.status === 'ready'
  const bullish = data?.bullish_pct ?? null
  const themes = data?.top_themes ?? []
  const showCrowd = crowdReady && (bullish !== null || themes.length > 0)

  // If nothing at all is available, render nothing rather than an empty shell.
  if (!business && !catalyst && !showCrowd) return null

  return (
    <div
      style={{
        margin: isMobile ? '16px 16px 0' : '20px 48px 0',
        background: '#1c1917',
        border: '1px solid #292524',
        borderRadius: 16,
        overflow: 'hidden',
      }}
    >
      {/* Header — on mobile it toggles expand/collapse */}
      <button
        onClick={() => isMobile && setExpanded(e => !e)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          padding: '12px 16px',
          background: 'transparent',
          border: 'none',
          textAlign: 'start',
          cursor: isMobile ? 'pointer' : 'default',
          color: '#fafaf9',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#a8a29e' }}>
          <Icon name="bolt" size={15} />
          Trader&apos;s Summary
        </span>
        {isMobile && (
          <Icon name={expanded ? 'expand_less' : 'expand_more'} size={20} style={{ color: '#a8a29e' }} />
        )}
      </button>

      {expanded && (
        <div style={{ padding: '0 16px 16px' }}>
          {/* 3 panels */}
          <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 12 }}>
            {business && (
              <div style={panelStyle}>
                <div style={panelLabelStyle}>Business</div>
                <div style={{ fontSize: 13, color: 'rgba(250,250,249,0.82)', lineHeight: 1.6 }}>{business}</div>
              </div>
            )}
            {catalyst && (
              <div style={panelStyle}>
                <div style={panelLabelStyle}>Catalyst</div>
                <div style={{ fontSize: 13, color: 'rgba(250,250,249,0.82)', lineHeight: 1.6 }}>{catalyst}</div>
              </div>
            )}
            {showCrowd && (
              <div style={panelStyle}>
                <div style={panelLabelStyle}>Crowd Signal</div>
                {bullish !== null && (
                  <div style={{ marginBottom: themes.length > 0 ? 12 : 0 }}>
                    <div style={{ fontSize: 11, color: '#a8a29e', marginBottom: 6, display: 'flex', justifyContent: 'space-between' }}>
                      <span>Bullish {bullish}%</span>
                      <span>Bearish {100 - bullish}%</span>
                    </div>
                    <div style={{ display: 'flex', height: 6, borderRadius: 999, overflow: 'hidden', background: '#292524' }}>
                      <div style={{ width: `${bullish}%`, background: '#22c55e' }} />
                      <div style={{ width: `${100 - bullish}%`, background: '#ef4444' }} />
                    </div>
                  </div>
                )}
                {themes.length > 0 && (
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {themes.slice(0, 3).map((theme, i) => (
                      <span
                        key={i}
                        style={{ fontSize: 11, fontWeight: 500, padding: '4px 10px', borderRadius: 999, border: '1px solid #292524', color: '#d6d3d1', background: '#1c1917' }}
                      >
                        {theme}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Quick-jump pills */}
          {sections.length > 0 && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
              {sections.map((s, i) => (
                <a
                  key={s.id}
                  href={`#section-${i}`}
                  style={{ fontSize: 12, fontWeight: 500, padding: '6px 12px', borderRadius: 999, border: '1px solid #292524', color: '#a8a29e', background: '#0c0a09', textDecoration: 'none', whiteSpace: 'nowrap' }}
                >
                  {s.title}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
