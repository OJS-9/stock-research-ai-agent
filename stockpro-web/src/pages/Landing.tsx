import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import Icon from '../components/Icon'
import { useBreakpoint } from '../hooks/useBreakpoint'

const features = [
  { icon: 'query_stats', name: 'Deep AI Research', desc: '12 specialized AI agents research fundamentals, technicals, risk, and news in parallel — synthesized into a structured research report.' },
  { icon: 'pie_chart', name: 'Portfolio Tracking', desc: 'Track multiple portfolios with real-time P&L, cost basis, sector allocation, and performance analytics.' },
  { icon: 'visibility', name: 'Smart Watchlists', desc: 'Organize symbols in custom lists with live prices, sparklines, earnings calendar, and AI-powered news briefings.' },
  { icon: 'notifications_active', name: 'Price Alerts', desc: 'Set price level alerts for any stock or crypto. Get notified the moment a condition triggers.' },
  { icon: 'forum', name: 'Report Chat', desc: 'After every research report, chat with the AI to dig deeper — it answers questions grounded in the report analysis.' },
]

const proof = [
  { num: '500+', label: 'Reports generated' },
  { num: '12', label: 'AI research agents' },
  { num: '<3 min', label: 'Avg report time' },
  { num: 'Real-time', label: 'Price alert notifications' },
]

export default function Landing() {
  const { isMobile } = useBreakpoint()
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)

  async function handleWaitlistSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const res = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok || data.ok === false) {
        setError(data.error || 'Something went wrong. Please try again.')
      } else {
        setShowModal(true)
        setEmail('')
      }
    } catch {
      setError('Network error. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  function scrollToWaitlist() {
    document.getElementById('waitlist-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const input = document.getElementById('waitlist-email') as HTMLInputElement | null
    setTimeout(() => input?.focus(), 400)
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0c0a09', color: '#fafaf9', fontFamily: 'Inter, Heebo, sans-serif', overflowX: 'hidden' }}>
      {/* NAV */}
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: isMobile ? '0 16px' : '0 64px',
          height: 64,
          position: 'sticky',
          top: 0,
          zIndex: 100,
          background: 'rgba(12,10,9,0.9)',
          backdropFilter: 'blur(16px)',
          borderBottom: '1px solid rgba(41,37,36,0.6)',
        }}
      >
        <span style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 18, fontWeight: 700, color: '#d6d3d1', letterSpacing: '-0.02em' }}>
          StockPro
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: isMobile ? 8 : 40, marginInlineStart: 'auto' }}>
          {!isMobile && (
            <div style={{ display: 'flex', gap: 32 }}>
              {[
                { label: 'Features', href: '#features' },
                { label: 'How it works', href: '#how-it-works' },
                { label: 'Pricing', href: '/pricing' },
              ].map(l => (
                <a key={l.label} href={l.href} style={{ color: '#a8a29e', textDecoration: 'none', fontSize: 14, fontWeight: 500, transition: 'color 0.15s' }}>
                  {l.label}
                </a>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <Link to="/sign-in" style={{ background: 'transparent', border: '1px solid #292524', color: '#a8a29e', fontSize: 13, fontWeight: 500, padding: '8px 14px', borderRadius: 8, textDecoration: 'none', display: 'inline-block' }}>
              Sign in
            </Link>
            <button
              type="button"
              onClick={scrollToWaitlist}
              style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 13, fontWeight: 600, padding: '8px 14px', borderRadius: 8, border: 'none', cursor: 'pointer', whiteSpace: 'nowrap' }}
            >
              Join waitlist
            </button>
          </div>
        </div>
      </nav>

      {/* HERO */}
      <section style={{ textAlign: 'center', padding: isMobile ? '56px 20px 48px' : '100px 64px 80px', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -200, left: '50%', transform: 'translateX(-50%)', width: 800, height: 600, borderRadius: '50%', background: 'radial-gradient(circle, rgba(34,197,94,0.06) 0%, transparent 60%)', pointerEvents: 'none' }} />
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 500, color: '#22c55e', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', padding: '5px 14px', borderRadius: 999, marginBottom: 28 }}>
          <Icon name="auto_awesome" filled size={14} />
          Powered by multi-agent AI
        </div>
        <h1 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 36 : 60, fontWeight: 800, lineHeight: 1.08, letterSpacing: '-0.04em', marginBottom: 24, maxWidth: 760, marginInlineStart: 'auto', marginInlineEnd: 'auto' }}>
          Deep company research for{' '}
          <em style={{ fontStyle: 'normal', color: '#22c55e' }}>curious learners</em>
        </h1>
        <p style={{ fontSize: isMobile ? 15 : 18, color: '#a8a29e', lineHeight: 1.7, maxWidth: 540, margin: '0 auto 40px' }}>
          Deep AI research reports on any stock or crypto in minutes. Portfolio tracking, watchlists, and price alerts built for people who want to understand companies.
        </p>
        <form
          id="waitlist-form"
          onSubmit={handleWaitlistSubmit}
          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, maxWidth: 480, margin: '0 auto' }}
        >
          <div style={{ display: 'flex', alignItems: 'stretch', gap: 8, width: '100%', flexDirection: isMobile ? 'column' : 'row' }}>
            <input
              id="waitlist-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@email.com"
              disabled={submitting}
              onFocus={(e) => { e.currentTarget.style.borderColor = '#22c55e' }}
              onBlur={(e) => { e.currentTarget.style.borderColor = '#292524' }}
              style={{ flex: 1, background: '#1c1917', border: '1px solid #292524', color: '#fafaf9', fontSize: 15, padding: '14px 16px', borderRadius: 10, outline: 'none', fontFamily: 'inherit', transition: 'border-color 0.15s' }}
            />
            <button
              type="submit"
              disabled={submitting}
              style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 15, fontWeight: 700, padding: '14px 28px', borderRadius: 10, border: 'none', cursor: submitting ? 'wait' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, whiteSpace: 'nowrap', opacity: submitting ? 0.7 : 1 }}
            >
              <Icon name="auto_awesome" size={18} />
              {submitting ? 'Joining...' : 'Join the waitlist'}
            </button>
          </div>
          {error && (
            <p style={{ fontSize: 13, color: '#ef4444', margin: 0, alignSelf: 'flex-start' }}>{error}</p>
          )}
        </form>
        <p style={{ fontSize: 12.5, color: '#a8a29e', marginTop: 16 }}>
          Early access &nbsp;&middot;&nbsp; We'll email you when your spot opens
        </p>
      </section>

      {/* SOCIAL PROOF */}
      <div style={{ display: isMobile ? 'grid' : 'flex', gridTemplateColumns: isMobile ? 'repeat(2, 1fr)' : undefined, alignItems: 'center', justifyContent: 'center', gap: isMobile ? 20 : 32, padding: isMobile ? '24px 20px' : '32px 64px', borderTop: '1px solid #292524', borderBottom: '1px solid #292524' }}>
        {proof.map(({ num, label }, i) => (
          <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 32, justifyContent: 'center' }}>
            {!isMobile && i > 0 && <div style={{ width: 1, height: 40, background: '#292524' }} />}
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 22 : 30, fontWeight: 700, color: '#fafaf9', letterSpacing: '-0.03em' }}>{num}</div>
              <div style={{ fontSize: 12, color: '#a8a29e', marginTop: 2 }}>{label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* APP PREVIEW */}
      <section style={{ padding: isMobile ? '48px 20px' : '80px 64px', textAlign: 'center' }}>
        <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#22c55e', marginBottom: 12 }}>The platform</div>
        <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 36, fontWeight: 700, letterSpacing: '-0.03em', marginBottom: 16 }}>
          Everything you need to research smarter
        </h2>
        <p style={{ fontSize: 15, color: '#a8a29e', maxWidth: 460, margin: '0 auto 48px' }}>
          Portfolio tracking, AI research, watchlists and price alerts in one dark, data-dense interface.
        </p>
        <div style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 18, overflow: 'hidden', maxWidth: 900, margin: '0 auto', boxShadow: '0 40px 80px rgba(0,0,0,0.6)' }}>
          <div style={{ background: 'rgba(12,10,9,0.9)', borderBottom: '1px solid #292524', padding: '14px 20px', display: 'flex', alignItems: 'center', gap: 16 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#292524', display: 'block' }} />
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#292524', display: 'block' }} />
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#292524', display: 'block' }} />
            </div>
            <div style={{ flex: 1, background: '#232120', border: '1px solid #292524', borderRadius: 6, padding: '5px 12px', fontSize: 12, color: '#a8a29e', textAlign: 'center' }}>
              app.stockpro.ai/dashboard
            </div>
          </div>
          <div style={{ padding: isMobile ? 16 : 28, display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr 1fr', gap: isMobile ? 12 : 16 }}>
            {[
              { label: 'Portfolio Value', val: '$142,830', sub: '+$1,204 today (+0.85%)', subColor: '#22c55e' },
              { label: 'Unrealized P&L', val: '+$18,430', valColor: '#22c55e', sub: '+14.8% all time', subColor: '#a8a29e' },
              { label: 'Active Alerts', val: '3', sub: '1 near trigger', subColor: '#22c55e' },
            ].map(({ label, val, valColor, sub, subColor }) => (
              <div key={label} style={{ background: '#232120', border: '1px solid #292524', borderRadius: 12, padding: 18 }}>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#a8a29e', marginBottom: 8 }}>{label}</div>
                <div style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 24, fontWeight: 600, color: valColor || '#fafaf9' }}>{val}</div>
                <div style={{ fontSize: 11, color: subColor, marginTop: 4 }}>{sub}</div>
              </div>
            ))}
            <div style={{ gridColumn: '1 / -1', background: '#232120', border: '1px solid #292524', borderRadius: 12, padding: 20, textAlign: 'start' }}>
              <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color: '#22c55e', marginBottom: 8 }}>AI Research &middot; NVDA &middot; Long thesis</div>
              <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>NVIDIA: Datacenter moat and AI infrastructure dominance</div>
              <div style={{ fontSize: 12, color: '#a8a29e', lineHeight: 1.6 }}>NVIDIA holds an estimated 80-90% share of the AI training accelerator market through CUDA ecosystem lock-in, H100/H200 demand backlog of 12+ months, and NVLink fabric advantages that competitors cannot replicate in the near term...</div>
            </div>
          </div>
        </div>
      </section>

      {/* FEATURES */}
      <section id="features" style={{ padding: isMobile ? '48px 20px' : '80px 64px', maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: isMobile ? 32 : 56 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#22c55e', marginBottom: 12 }}>Features</div>
          <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 36, fontWeight: 700, letterSpacing: '-0.03em', marginBottom: 14 }}>Built for people who want to understand companies</h2>
          <p style={{ fontSize: 15, color: '#a8a29e', maxWidth: 480, margin: '0 auto' }}>Every feature designed to help you understand companies deeply.</p>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: isMobile ? 14 : 20 }}>
          {features.map(({ icon, name, desc }) => (
            <div key={name} style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 16, padding: 28, transition: 'border-color 0.2s', width: isMobile ? '100%' : 'calc(33.333% - 14px)', flexShrink: 0 }}>
              <div style={{ width: 44, height: 44, borderRadius: 12, background: '#232120', border: '1px solid #292524', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 18 }}>
                <Icon name={icon} size={22} />
              </div>
              <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 10 }}>{name}</div>
              <div style={{ fontSize: 13.5, color: '#a8a29e', lineHeight: 1.65 }}>{desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how-it-works" style={{ padding: isMobile ? '48px 20px' : '80px 64px', background: '#1c1917', borderTop: '1px solid #292524', borderBottom: '1px solid #292524' }}>
        <div style={{ maxWidth: 800, margin: '0 auto', textAlign: 'center' }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#22c55e', marginBottom: 12 }}>How it works</div>
          <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 36, fontWeight: 700, letterSpacing: '-0.03em' }}>Research any ticker in 3 steps</h2>
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: isMobile ? 28 : 40, marginTop: 48, position: 'relative' }}>
            {!isMobile && <div style={{ position: 'absolute', top: 22, left: '15%', right: '15%', height: 1, background: 'linear-gradient(90deg, transparent, #292524, transparent)' }} />}
            {[
              { n: '1', title: 'Enter a ticker', desc: 'Type any stock or crypto symbol and select your research focus — long, short, or comprehensive.' },
              { n: '2', title: 'AI agents research', desc: '12 parallel AI agents gather fundamentals, news, technicals, risk factors, and competitive landscape.' },
              { n: '3', title: 'Read & chat', desc: 'Get a full research report and chat with the AI to explore any section in depth.' },
            ].map(({ n, title, desc }) => (
              <div key={n} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 }}>
                <div style={{ width: 44, height: 44, borderRadius: '50%', background: '#0c0a09', border: '1px solid #292524', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 16, fontWeight: 700, color: '#d6d3d1', position: 'relative', zIndex: 1 }}>{n}</div>
                <div style={{ fontSize: 15, fontWeight: 600 }}>{title}</div>
                <div style={{ fontSize: 13, color: '#a8a29e', textAlign: 'center', lineHeight: 1.6 }}>{desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" style={{ padding: isMobile ? '48px 20px' : '80px 64px', maxWidth: 820, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: isMobile ? 32 : 48 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#22c55e', marginBottom: 12 }}>Frequently asked questions</div>
          <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 36, fontWeight: 700, letterSpacing: '-0.03em' }}>Common questions about AI stock research</h2>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {[
            { q: 'What makes StockPro different from a normal stock screener?', a: "StockPro runs 12 specialized AI research agents in parallel — fundamentals, technicals, risk, competitive landscape, news sentiment, and more — and synthesizes their output into a structured research report. Traditional screeners filter tickers by metrics; StockPro explains the company." },
            { q: 'How does the AI research actually work?', a: 'A planner agent reads your ticker and picks which specialists to dispatch. Up to 12 expert agents — fundamentals, technicals, risk, competitive landscape, news sentiment, and more — research in parallel using live market data and real-time web sources. A quality gate filters weak outputs before a synthesis agent merges everything into one cohesive report.' },
            { q: 'Is StockPro free to use?', a: 'Yes. StockPro has a free tier with no credit card required — you can generate research reports, track a portfolio, build watchlists, and set price alerts. Paid plans add higher report limits and premium features.' },
            { q: 'Can AI replace a human stock analyst?', a: "No, and we don't pitch it as a replacement. AI is best at quickly synthesizing structured information — earnings, filings, news, technicals — so you can form your own view faster. Final investment decisions still require human judgment about risk tolerance, time horizon, and conviction." },
            { q: 'How accurate is AI-generated stock research?', a: 'Accuracy depends on the data sources and the reasoning. Every report is grounded in real-time market data, financial filings, and live web sources, and a quality gate runs before synthesis to reject low-confidence outputs. Reports are informational, not investment advice.' },
            { q: 'What data sources does AI stock research use?', a: 'StockPro pulls live market data and fundamentals for stocks and crypto, real-time news sentiment, and live web search for context and breaking events. Reports cite the underlying sources where applicable.' },
            { q: "What's the best AI tool for researching stocks?", a: 'StockPro is purpose-built for stock and crypto research — unlike general chatbots, it runs a multi-agent research pipeline that gathers fundamentals, technicals, risk, and news in parallel, then synthesizes a structured report you can chat with. Free to try at stock-pro.org.' },
            { q: 'How do I track a stock portfolio with AI?', a: 'In StockPro, you create one or more portfolios, add transactions (buy/sell, manually or via CSV), and the app tracks cost basis, P&L, sector allocation, and history automatically. AI research is then framed against your actual positions.' },
            { q: 'Can I get AI alerts when a stock hits a target price?', a: 'Yes. StockPro lets you set price-level alerts for any stock or crypto. When the trigger fires, you get an in-app notification and an optional Telegram message — no need to keep checking quotes.' },
          ].map(({ q, a }) => (
            <div key={q} style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 14, padding: isMobile ? 20 : 24 }}>
              <h3 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 16, fontWeight: 600, margin: 0, marginBottom: 10, color: '#fafaf9' }}>{q}</h3>
              <p style={{ fontSize: 14, color: '#a8a29e', lineHeight: 1.65, margin: 0 }}>{a}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section style={{ padding: isMobile ? '64px 20px' : '100px 64px', textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at center, rgba(34,197,94,0.05) 0%, transparent 65%)', pointerEvents: 'none' }} />
        <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 28 : 44, fontWeight: 800, letterSpacing: '-0.04em', marginBottom: 16 }}>Be first in line for early access</h2>
        <p style={{ fontSize: 16, color: '#a8a29e', marginBottom: 36 }}>Join the waitlist. We'll email you when your spot opens.</p>
        <button
          type="button"
          onClick={scrollToWaitlist}
          style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 15, fontWeight: 700, padding: '14px 32px', borderRadius: 10, display: 'inline-flex', alignItems: 'center', gap: 8, border: 'none', cursor: 'pointer' }}
        >
          <Icon name="auto_awesome" size={18} />
          Join the waitlist
        </button>
      </section>

      {/* FOOTER */}
      <footer style={{ borderTop: '1px solid #292524', padding: isMobile ? '24px 20px' : '32px 64px', display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 14 : 0, alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 16, fontWeight: 700, color: '#d6d3d1' }}>StockPro</span>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Link to="/about" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>About</Link>
          <Link to="/press" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>Press</Link>
          <Link to="/legal/privacy" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>Privacy</Link>
          <Link to="/legal/terms" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>Terms</Link>
          <Link to="/legal/refund" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>Refund</Link>
        </div>
        <span style={{ fontSize: 12, color: '#a8a29e' }}>&copy; 2026 StockPro</span>
      </footer>

      {showModal && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setShowModal(false)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(12,10,9,0.8)', backdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200, padding: 20 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 18, padding: isMobile ? 28 : 36, maxWidth: 440, width: '100%', textAlign: 'center', boxShadow: '0 40px 80px rgba(0,0,0,0.6)' }}
          >
            <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'rgba(34,197,94,0.12)', border: '1px solid rgba(34,197,94,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
              <Icon name="check" size={28} />
            </div>
            <h3 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 10px' }}>
              You're on the list
            </h3>
            <p style={{ fontSize: 14.5, color: '#a8a29e', lineHeight: 1.6, margin: '0 0 24px' }}>
              Thanks for joining the StockPro waitlist. We'll email you the moment your spot opens — usually within a couple of weeks.
            </p>
            <button
              type="button"
              onClick={() => setShowModal(false)}
              style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 14, fontWeight: 600, padding: '12px 28px', borderRadius: 10, border: 'none', cursor: 'pointer' }}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
