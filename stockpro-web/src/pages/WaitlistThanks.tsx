import { Link } from 'react-router'
import Icon from '../components/Icon'
import { useBreakpoint } from '../hooks/useBreakpoint'

export default function WaitlistThanks() {
  const { isMobile } = useBreakpoint()

  return (
    <div style={{ minHeight: '100vh', background: '#0c0a09', color: '#fafaf9', fontFamily: 'Inter, Heebo, sans-serif', display: 'flex', flexDirection: 'column' }}>
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: isMobile ? '0 16px' : '0 64px',
          height: 64,
          borderBottom: '1px solid rgba(41,37,36,0.6)',
          background: 'rgba(12,10,9,0.9)',
          backdropFilter: 'blur(16px)',
        }}
      >
        <Link to="/" style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: 18, fontWeight: 700, color: '#d6d3d1', letterSpacing: '-0.02em', textDecoration: 'none' }}>
          StockPro
        </Link>
      </nav>

      <main style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24, position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', top: '20%', left: '50%', transform: 'translateX(-50%)', width: 800, height: 600, borderRadius: '50%', background: 'radial-gradient(circle, rgba(34,197,94,0.06) 0%, transparent 60%)', pointerEvents: 'none' }} />
        <div style={{ background: '#1c1917', border: '1px solid #292524', borderRadius: 20, padding: isMobile ? 32 : 48, maxWidth: 460, width: '100%', textAlign: 'center', boxShadow: '0 40px 80px rgba(0,0,0,0.6)', position: 'relative' }}>
          <div style={{ width: 64, height: 64, borderRadius: '50%', background: 'rgba(34,197,94,0.12)', border: '1px solid rgba(34,197,94,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 24px' }}>
            <Icon name="mark_email_read" size={32} />
          </div>
          <h1 style={{ fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontSize: isMobile ? 26 : 32, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 14px' }}>
            You're on the list
          </h1>
          <p style={{ fontSize: 15, color: '#a8a29e', lineHeight: 1.6, margin: '0 0 32px' }}>
            Thanks for joining the StockPro waitlist. We'll email you the moment your spot opens — usually within a couple of weeks.
          </p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexDirection: isMobile ? 'column' : 'row' }}>
            <Link
              to="/"
              style={{ background: '#d6d3d1', color: '#0c0a09', fontSize: 14, fontWeight: 600, padding: '12px 24px', borderRadius: 10, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
            >
              Back to home
            </Link>
            <Link
              to="/sign-in"
              style={{ background: 'transparent', border: '1px solid #292524', color: '#a8a29e', fontSize: 14, fontWeight: 500, padding: '12px 24px', borderRadius: 10, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
            >
              Sign in
            </Link>
          </div>
        </div>
      </main>

      <footer style={{ borderTop: '1px solid #292524', padding: isMobile ? '20px' : '24px 64px', textAlign: 'center' }}>
        <span style={{ fontSize: 12, color: '#a8a29e' }}>&copy; 2026 StockPro</span>
      </footer>
    </div>
  )
}
