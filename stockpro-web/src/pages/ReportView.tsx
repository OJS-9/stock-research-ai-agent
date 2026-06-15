import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { useTranslation } from 'react-i18next'
import AppNav from '../components/AppNav'
import Icon from '../components/Icon'
import TraderSummaryCard from '../components/TraderSummaryCard'
import { useApiClient } from '../api/client'
import { useLanguage } from '../LanguageContext'
import { useBreakpoint } from '../hooks/useBreakpoint'

const tocIcons: Record<string, string> = {
  'Executive Summary': 'summarize',
  'Business Overview': 'trending_up',
  'Financial Analysis': 'analytics',
  'Valuation': 'account_balance',
  'Competitive Landscape': 'groups',
  'Growth Catalysts': 'psychology',
  'Risk Factors': 'warning',
  'Technical Analysis': 'show_chart',
  'Conclusion': 'check_circle',
}

export default function ReportView() {
  const { id } = useParams()
  const api = useApiClient()
  const [activeSection, setActiveSection] = useState(0)
  const contentRef = useRef<HTMLDivElement>(null)
  const { t } = useTranslation()
  const { lang } = useLanguage()
  const { isMobile } = useBreakpoint()
  const [tocOpen, setTocOpen] = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)

  const handleExportPdf = async () => {
    setExportingPdf(true)
    try {
      const res = await api.get(`/report/${id}/pdf`)
      if (!res.ok) return
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${report.symbol}_report.pdf`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setExportingPdf(false)
    }
  }

  // /report/<id>?format=json returns {report: {report_id, ticker, trade_type, report_text, created_at, ...}}
  const { data } = useQuery({
    queryKey: ['report', id],
    queryFn: async () => {
      const res = await api.get(`/api/report/${id}`)
      if (!res.ok) throw new Error('Failed')
      return res.json()
    },
  })

  // /api/report/<id>/sections returns {sections: [string, ...]}
  const { data: sectionsData } = useQuery({
    queryKey: ['report-sections', id],
    queryFn: async () => {
      const res = await api.get(`/api/report/${id}/sections`)
      if (!res.ok) return null
      return res.json()
    },
  })

  const locale = lang === 'he' ? 'he-IL' : 'en-US'

  // API report shape: report_id, ticker, trade_type, report_text, created_at
  const rawReport = data?.report || data
  const report = rawReport ? {
    id: rawReport.report_id || id,
    symbol: rawReport.ticker || rawReport.symbol || '?',
    title: rawReport.title || `${rawReport.ticker || ''} Research Report`,
    type: rawReport.trade_type || rawReport.type || '',
    created_at: rawReport.created_at ? new Date(rawReport.created_at).toLocaleDateString(locale, { month: 'long', day: 'numeric', year: 'numeric' }) : '',
    word_count: rawReport.report_text ? Math.round(rawReport.report_text.split(/\s+/).length) : 0,
    report_text: rawReport.report_text || '',
  } : { id, symbol: '', title: '', type: '', created_at: '', word_count: 0, report_text: '' }

  // Sections API returns string array — convert to section objects for display
  // If no sections data yet, parse headings from report_text
  const sectionNames: string[] = sectionsData?.sections || []
  const sections = sectionNames.length > 0
    ? sectionNames.map((title: string, i: number) => ({ id: `s${i}`, title }))
    : report.report_text
      ? (() => {
          // Parse ## headings from markdown
          const headings = report.report_text.match(/^#{1,3}\s+.+$/gm) || []
          return headings.map((h: string, i: number) => ({
            id: `s${i}`,
            title: h.replace(/^#+\s+/, '').trim(),
          }))
        })()
      : []

  useEffect(() => {
    if (!contentRef.current) return
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(e => {
          if (e.isIntersecting) {
            const idx = parseInt(e.target.getAttribute('data-section') || '0')
            setActiveSection(idx)
          }
        })
      },
      { threshold: 0.3 }
    )
    contentRef.current.querySelectorAll('[data-section]').forEach(el => observer.observe(el))
    return () => observer.disconnect()
  }, [sections])

  return (
    <div style={{ background: '#0c0a09', color: '#fafaf9' }}>
      <AppNav />
      <div style={{ display: 'flex', height: 'calc(100vh - 60px)', overflow: 'hidden' }}>

        {/* LEFT TOC */}
        {isMobile && tocOpen && (
          <div onClick={() => setTocOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 150 }} />
        )}
        <aside style={{
          width: isMobile ? 260 : 232,
          flexShrink: 0,
          borderInlineEnd: '1px solid #292524',
          padding: '28px 16px',
          overflowY: 'auto',
          background: '#0c0a09',
          display: 'flex',
          flexDirection: 'column',
          gap: 24,
          ...(isMobile ? {
            position: 'fixed',
            top: 60,
            bottom: 0,
            insetInlineStart: tocOpen ? 0 : -280,
            zIndex: 160,
            transition: 'inset-inline-start 0.25s',
          } : {}),
        }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#a8a29e', paddingInlineStart: 12, marginBottom: 8 }}>{t('reportView.contents')}</div>
            {sections.map((s: any, i: number) => {
              const icon = tocIcons[s.title] || 'article'
              return (
                <a
                  key={s.id}
                  href={`#section-${i}`}
                  onClick={() => setActiveSection(i)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8, fontSize: 13, color: activeSection === i ? '#fafaf9' : '#a8a29e', background: activeSection === i ? 'rgba(214,211,209,0.08)' : 'transparent', textDecoration: 'none', marginBottom: 2, transition: 'all 0.15s' }}
                >
                  <Icon name={icon} size={15} />
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</span>
                </a>
              )
            })}
          </div>
          <div style={{ marginTop: 'auto', paddingTop: 16, borderTop: '1px solid #292524' }}>
            {[
              { icon: 'description', text: t('reportView.words', { count: report.word_count || 5400 }) },
              { icon: 'schedule', text: report.created_at },
            ].map(({ icon, text }) => (
              <div key={text} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#a8a29e', padding: '6px 12px' }}>
                <Icon name={icon} size={15} />
                {text}
              </div>
            ))}
            {isMobile && (
              <button
                onClick={() => { handleExportPdf(); setTocOpen(false) }}
                disabled={exportingPdf}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  fontSize: 12, color: '#a8a29e',
                  padding: '6px 12px',
                  background: 'none', border: 'none',
                  width: '100%', textAlign: 'start',
                  cursor: exportingPdf ? 'wait' : 'pointer',
                  opacity: exportingPdf ? 0.5 : 1,
                }}
              >
                <Icon name="download" size={15} />
                {exportingPdf ? t('reportView.exporting', 'Exporting...') : t('reportView.exportPdf')}
              </button>
            )}
          </div>
        </aside>

        {/* MAIN */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {/* STICKY HEADER */}
          <div style={{ padding: isMobile ? '12px 16px' : '24px 48px', borderBottom: '1px solid #292524', position: 'sticky', top: 0, background: 'rgba(12,10,9,0.95)', backdropFilter: 'blur(12px)', zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
              {isMobile && (
                <button onClick={() => setTocOpen(o => !o)} style={{ background: 'none', border: '1px solid #292524', borderRadius: 8, padding: 6, color: '#a8a29e', cursor: 'pointer', display: 'flex' }}>
                  <Icon name="menu_book" size={18} />
                </button>
              )}
              <span style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 13, fontWeight: 700, padding: '5px 12px', borderRadius: 6, letterSpacing: '0.02em', fontFamily: 'Nunito, "Secular One", Heebo, sans-serif' }}>

                {report.symbol}
              </span>
              <span style={{ fontSize: 12, fontWeight: 500, padding: '4px 10px', borderRadius: 999, background: 'rgba(34,197,94,0.08)', color: '#22c55e', border: '1px solid rgba(34,197,94,0.2)' }}>
                {report.type}
              </span>
              <span style={{ fontSize: 13, color: '#a8a29e' }}>{report.created_at}</span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              {!isMobile && (
                <>
                  <button style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'transparent', border: '1px solid #292524', color: '#a8a29e', fontSize: 12.5, fontWeight: 500, padding: '7px 14px', borderRadius: 8, cursor: 'pointer' }}>
                    <Icon name="share" size={15} /> {t('reportView.share')}
                  </button>
                  <button
                    disabled={exportingPdf}
                    onClick={handleExportPdf}
                    style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'transparent', border: '1px solid #292524', color: '#a8a29e', fontSize: 12.5, fontWeight: 500, padding: '7px 14px', borderRadius: 8, cursor: exportingPdf ? 'wait' : 'pointer', opacity: exportingPdf ? 0.5 : 1 }}
                  >
                    <Icon name="download" size={15} /> {exportingPdf ? t('reportView.exporting', 'Exporting...') : t('reportView.exportPdf')}
                  </button>
                </>
              )}
              <Link
                to={`/chat/${id}`}
                style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 13, fontWeight: 600, padding: '8px 16px', borderRadius: 8, border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, textDecoration: 'none' }}
              >
                <Icon name="forum" size={16} /> {t('reportView.chatWithAi')}
              </Link>
            </div>
          </div>

          {/* TRADER'S SUMMARY (issue #134): at-a-glance card above the report body */}
          <TraderSummaryCard
            reportText={report.report_text}
            symbol={report.symbol}
            sections={sections}
            isMobile={isMobile}
          />

          {/* CONTENT */}
          <div ref={contentRef} style={{ padding: isMobile ? '24px 16px 60px' : '40px 48px 100px', maxWidth: 740 }}>
            {/* POST-REPORT CTA: bridge the user from reading to acting (issue #111) */}
            {report.symbol && report.symbol !== '?' && (
              <div style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 14, padding: isMobile ? '16px' : '20px 24px', marginBottom: 32 }}>
                <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#a8a29e', marginBottom: 14 }}>{t('reportView.cta.heading')}</div>
                <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 12 }}>
                  <Link
                    to={`/portfolio?add=${encodeURIComponent(report.symbol)}`}
                    style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: '#d6d3d1', color: '#0c0a09', fontSize: 13.5, fontWeight: 600, padding: '12px 16px', borderRadius: 10, textDecoration: 'none' }}
                  >
                    <Icon name="account_balance_wallet" size={18} /> {t('reportView.cta.addToPortfolio', { symbol: report.symbol })}
                  </Link>
                  <Link
                    to={`/alerts?new=1&symbol=${encodeURIComponent(report.symbol)}`}
                    style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, background: 'transparent', border: '1px solid #292524', color: '#fafaf9', fontSize: 13.5, fontWeight: 600, padding: '12px 16px', borderRadius: 10, textDecoration: 'none' }}
                  >
                    <Icon name="notifications_active" size={18} /> {t('reportView.cta.setAlert', { symbol: report.symbol })}
                  </Link>
                </div>
              </div>
            )}
            <h1 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 20 : 28, fontWeight: 700, letterSpacing: '-0.02em', marginBottom: 32, lineHeight: 1.25 }}>

              {report.title || `${report.symbol} ${t('reportView.researchReport')}`}
            </h1>

            {report.report_text ? (
              <div
                style={{ fontSize: 14, color: 'rgba(250,250,249,0.8)', lineHeight: 1.85 }}
                dangerouslySetInnerHTML={{ __html: (() => {
                  // Simple markdown-to-HTML conversion for display.
                  // Headings get sequential id="section-N" anchors (document order)
                  // so the left TOC and the Trader's Summary quick-jump pills scroll.
                  let headingIdx = 0
                  let html = report.report_text
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/^(#{1,3})\s+(.+)$/gm, (_m: string, hashes: string, text: string) => {
                      const id = `section-${headingIdx++}`
                      const level = hashes.length
                      if (level === 3) return `<h3 id="${id}" style="font-family:Nunito,sans-serif;font-size:17px;font-weight:700;margin:28px 0 10px;color:#fafaf9">${text}</h3>`
                      if (level === 2) return `<h2 id="${id}" style="font-family:Nunito,sans-serif;font-size:20px;font-weight:700;margin:36px 0 14px;color:#fafaf9">${text}</h2>`
                      return `<h1 id="${id}" style="font-family:Nunito,sans-serif;font-size:24px;font-weight:700;margin:40px 0 16px;color:#fafaf9">${text}</h1>`
                    })
                    .replace(/\*\*(.+?)\*\*/g, '<strong style="color:#fafaf9;font-weight:600">$1</strong>')
                    .replace(/\*(.+?)\*/g, '<em>$1</em>')
                    .replace(/^[-*]\s+(.+)$/gm, '<li style="margin-bottom:6px">$1</li>')
                    .replace(/(<li.*<\/li>\n?)+/g, '<ul style="padding-inline-start:20px;margin:12px 0">$&</ul>')
                    .replace(/\n\n/g, '</p><p style="margin:0 0 14px">')
                    .replace(/^(?!<[h|ul|li])(.+)$/gm, (m: string) => m.startsWith('<') ? m : `<p style="margin:0 0 14px">${m}</p>`)
                  return html
                })() }}
              />
            ) : (
              sections.map((s: any, i: number) => (
                <div key={s.id} id={`section-${i}`} data-section={i} style={{ marginBottom: 40 }}>
                  <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', margin: '44px 0 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ fontSize: 13, fontWeight: 500, color: '#a8a29e', minWidth: 24 }}>{String(i + 1).padStart(2, '0')}</span>
                    {s.title}
                  </h2>
                  {i < sections.length - 1 && <hr style={{ border: 'none', borderTop: '1px solid #292524', margin: '36px 0' }} />}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
