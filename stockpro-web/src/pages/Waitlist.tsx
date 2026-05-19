import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import Icon from '../components/Icon'
import { useBreakpoint } from '../hooks/useBreakpoint'

const steps = [
  { n: '1', title: 'Enter a ticker', desc: 'Type any US stock or crypto ticker and choose your research focus — long, short, or comprehensive.' },
  { n: '2', title: 'Agents run in parallel', desc: 'Up to 12 expert agents research fundamentals, technicals, risk, news, and competitive landscape simultaneously.' },
  { n: '3', title: 'Get your report', desc: 'A synthesis agent merges all findings into one cohesive report — and you can chat with it to go deeper.' },
]

const audiences = [
  { icon: 'bolt', title: 'Active traders', desc: 'Rapid pre-trade context on any ticker — catalysts, technicals, and sector momentum in one report.' },
  { icon: 'trending_up', title: 'Long-term researchers', desc: 'Deep-dive fundamentals: valuation, competitive moat, management quality, and growth drivers.' },
  { icon: 'currency_bitcoin', title: 'Crypto enthusiasts', desc: 'Research BTC, ETH, and altcoins alongside equities. Track your portfolio across both asset classes.' },
]

export default function Waitlist() {
  const { isMobile } = useBreakpoint()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
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
        setSubmitting(false)
      } else {
        navigate('/waitlist/thanks')
      }
    } catch {
      setError('Network error. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0c0a09', color: '#fafaf9', fontFamily: 'Inter, Heebo, sans-serif', overflowX: 'hidden' }}>
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
        <Link to="/" style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 18, fontWeight: 700, color: '#d6d3d1', letterSpacing: '-0.02em', textDecoration: 'none' }}>
          StockPro
        </Link>
        <Link to="/sign-in" style={{ background: 'transparent', border: '1px solid #292524', color: '#a8a29e', fontSize: 13, fontWeight: 500, padding: '8px 14px', borderRadius: 8, textDecoration: 'none' }}>
          Sign in
        </Link>
      </nav>

      <section style={{ textAlign: 'center', padding: isMobile ? '56px 20px 48px' : '100px 64px 72px', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: -200, left: '50%', transform: 'translateX(-50%)', width: 800, height: 600, borderRadius: '50%', background: 'radial-gradient(circle, rgba(34,197,94,0.06) 0%, transparent 60%)', pointerEvents: 'none' }} />
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 500, color: '#22c55e', background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', padding: '5px 14px', borderRadius: 999, marginBottom: 28 }}>
          <Icon name="auto_awesome" filled size={14} />
          Early access — limited spots
        </div>
        <h1 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 36 : 60, fontWeight: 800, lineHeight: 1.08, letterSpacing: '-0.04em', marginBottom: 24, maxWidth: 760, marginInlineStart: 'auto', marginInlineEnd: 'auto' }}>
          Deep company research.{' '}
          <em style={{ fontStyle: 'normal', color: '#22c55e' }}>In minutes, not hours.</em>
        </h1>
        <p style={{ fontSize: isMobile ? 15 : 18, color: '#a8a29e', lineHeight: 1.7, maxWidth: 540, margin: '0 auto 40px' }}>
          StockPro deploys a 12-agent AI pipeline to produce the depth of research that used to take analysts hours — delivered in a single click.
        </p>
        <form
          onSubmit={handleSubmit}
          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, maxWidth: 480, margin: '0 auto' }}
        >
          <div style={{ display: 'flex', alignItems: 'stretch', gap: 8, width: '100%', flexDirection: isMobile ? 'column' : 'row' }}>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@email.com"
              disabled={submitting}
              style={{ flex: 1, background: '#1c1917', border: '1px solid #292524', color: '#fafaf9', fontSize: 15, padding: '14px 16px', borderRadius: 10, outline: 'none', fontFamily: 'inherit' }}
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
          No spam &nbsp;&middot;&nbsp; Unsubscribe anytime
        </p>
      </section>

      <section style={{ padding: isMobile ? '48px 20px' : '64px 64px', maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: isMobile ? 32 : 48 }}>
          <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#22c55e', marginBottom: 12 }}>How it works</div>
          <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 36, fontWeight: 700, letterSpacing: '-0.03em', marginBottom: 14 }}>How the agents work</h2>
          <p style={{ fontSize: 15, color: '#a8a29e', maxWidth: 480, margin: '0 auto' }}>Each report runs 12 specialized AI agents in parallel — the same coverage a sell-side team would produce over days.</p>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: isMobile ? 14 : 20 }}>
          {steps.map(({ n, title, desc }) => (
            <div key={n} style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 16, padding: 24 }}>
              <div style={{ width: 36, height: 36, borderRadius: 10, background: '#232120', border: '1px solid #292524', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 14, fontWeight: 700, color: '#d6d3d1', marginBottom: 16 }}>{n}</div>
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{title}</div>
              <div style={{ fontSize: 13, color: '#a8a29e', lineHeight: 1.6 }}>{desc}</div>
            </div>
          ))}
        </div>
      </section>

      <section style={{ padding: isMobile ? '48px 20px' : '64px 64px', background: '#1c1917', borderTop: '1px solid #292524', borderBottom: '1px solid #292524' }}>
        <div style={{ maxWidth: 1000, margin: '0 auto' }}>
          <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 24 : 32, fontWeight: 700, letterSpacing: '-0.03em', textAlign: 'center', marginBottom: 32 }}>
            Built for people who want to understand companies
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: isMobile ? 14 : 20 }}>
            {audiences.map(({ icon, title, desc }) => (
              <div key={title} style={{ background: '#0c0a09', border: '1px solid #292524', borderRadius: 16, padding: 24 }}>
                <div style={{ width: 40, height: 40, borderRadius: 10, background: '#232120', border: '1px solid #292524', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 16 }}>
                  <Icon name={icon} size={20} />
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>{title}</div>
                <div style={{ fontSize: 13, color: '#a8a29e', lineHeight: 1.6 }}>{desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section style={{ padding: isMobile ? '56px 20px' : '88px 64px', textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(ellipse at center, rgba(34,197,94,0.05) 0%, transparent 65%)', pointerEvents: 'none' }} />
        <h2 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 40, fontWeight: 800, letterSpacing: '-0.04em', marginBottom: 12 }}>Be first when we launch</h2>
        <p style={{ fontSize: 15, color: '#a8a29e', marginBottom: 28 }}>Early access users get priority onboarding and locked-in pricing.</p>
        <form
          onSubmit={handleSubmit}
          style={{ display: 'flex', alignItems: 'stretch', gap: 8, width: '100%', maxWidth: 480, margin: '0 auto', flexDirection: isMobile ? 'column' : 'row' }}
        >
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@email.com"
            disabled={submitting}
            style={{ flex: 1, background: '#1c1917', border: '1px solid #292524', color: '#fafaf9', fontSize: 15, padding: '14px 16px', borderRadius: 10, outline: 'none', fontFamily: 'inherit' }}
          />
          <button
            type="submit"
            disabled={submitting}
            style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 15, fontWeight: 700, padding: '14px 28px', borderRadius: 10, border: 'none', cursor: submitting ? 'wait' : 'pointer', whiteSpace: 'nowrap', opacity: submitting ? 0.7 : 1 }}
          >
            {submitting ? 'Joining...' : 'Join the waitlist'}
          </button>
        </form>
      </section>

      <footer style={{ borderTop: '1px solid #292524', padding: isMobile ? '24px 20px' : '32px 64px', display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 14 : 0, alignItems: 'center', justifyContent: 'space-between' }}>
        <Link to="/" style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 16, fontWeight: 700, color: '#d6d3d1', textDecoration: 'none' }}>StockPro</Link>
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', justifyContent: 'center' }}>
          <Link to="/about" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>About</Link>
          <Link to="/legal/privacy" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>Privacy</Link>
          <Link to="/legal/terms" style={{ fontSize: 13, color: '#a8a29e', textDecoration: 'none' }}>Terms</Link>
        </div>
        <span style={{ fontSize: 12, color: '#a8a29e' }}>&copy; 2026 StockPro</span>
      </footer>
    </div>
  )
}
