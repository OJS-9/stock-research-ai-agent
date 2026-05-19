import { useRef, useEffect, lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router'
import { SignedIn, SignedOut } from '@clerk/clerk-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useApiClient } from './api/client'

// Eager: small pages needed immediately
import Landing from './pages/Landing'
import SignIn from './pages/SignIn'
import Terms from './pages/legal/Terms'
import Privacy from './pages/legal/Privacy'
import Refund from './pages/legal/Refund'
import About from './pages/About'
import Press from './pages/Press'
import Waitlist from './pages/Waitlist'
import WaitlistThanks from './pages/WaitlistThanks'

// Lazy: all authenticated pages
const Home = lazy(() => import('./pages/Home'))
const PortfolioList = lazy(() => import('./pages/PortfolioList'))
const PortfolioDetail = lazy(() => import('./pages/PortfolioDetail'))
const AddTransaction = lazy(() => import('./pages/AddTransaction'))
const ImportCSV = lazy(() => import('./pages/ImportCSV'))
const HoldingDetail = lazy(() => import('./pages/HoldingDetail'))
const Analytics = lazy(() => import('./pages/Analytics'))
const ReportsHistory = lazy(() => import('./pages/ReportsHistory'))
const ReportView = lazy(() => import('./pages/ReportView'))
const Chat = lazy(() => import('./pages/Chat'))
const ResearchWizard = lazy(() => import('./pages/ResearchWizard'))
const Watchlist = lazy(() => import('./pages/Watchlist'))
const Alerts = lazy(() => import('./pages/Alerts'))
const TickerPage = lazy(() => import('./pages/TickerPage'))
const Settings = lazy(() => import('./pages/Settings'))
const DevicePage = lazy(() => import('./pages/DevicePage'))
const Pricing = lazy(() => import('./pages/Pricing'))
const BillingReturn = lazy(() => import('./pages/BillingReturn'))

/**
 * Runs at app level (never unmounts on navigation) so toast dedup works.
 * Only toasts notifications arriving AFTER mount to avoid flooding on login.
 */
function NotificationListener() {
  const api = useApiClient()
  const shownIds = useRef<Set<string>>(new Set())
  const baselineTs = useRef<string | null>(null)
  const initialized = useRef(false)

  const { data } = useQuery({
    queryKey: ['alert-notifications'],
    queryFn: async () => {
      const res = await api.get('/api/alerts/notifications?limit=20')
      if (!res.ok) return { notifications: [], unread_count: 0 }
      return res.json()
    },
    refetchInterval: 30_000,
    staleTime: 25_000,
  })

  const notifications: any[] = data?.notifications ?? []

  if (notifications.length > 0 && !initialized.current) {
    baselineTs.current = notifications[0]?.created_at ?? null
    initialized.current = true
  } else if (initialized.current) {
    const newUnread = notifications.filter(
      (n: any) =>
        !n.read_at &&
        !shownIds.current.has(n.notification_id) &&
        baselineTs.current &&
        n.created_at > baselineTs.current
    )
    if (newUnread.length > 0) {
      newUnread.forEach((n: any) => {
        shownIds.current.add(n.notification_id)
        toast(n.body, {
          duration: 6000,
          style: {
            background: '#1c1917',
            color: '#fafaf9',
            border: '1px solid #292524',
            fontSize: 13,
          },
        })
      })
    }
  }

  return null
}

/** Prefetch all page data once on login so navigation is instant. */
function DataPrefetcher() {
  const api = useApiClient()
  const queryClient = useQueryClient()

  useEffect(() => {
    const fetchJson = async (url: string) => {
      const res = await api.get(url)
      if (!res.ok) throw new Error('Failed')
      return res.json()
    }

    const pages = [
      { key: ['home'], url: '/api/home?refresh_news=1' },
      { key: ['portfolios'], url: '/api/portfolios/prices' },
      { key: ['watchlists'], url: '/api/watchlists' },
      { key: ['reports'], url: '/api/reports' },
      { key: ['alerts'], url: '/api/alerts' },
    ]
    pages.forEach(({ key, url }) => {
      queryClient.prefetchQuery({
        queryKey: key,
        queryFn: () => fetchJson(url),
        staleTime: 120_000,
      })
    })

    // Prefetch portfolios list, then seed each portfolio's price cache
    // so detail pages render instantly without extra API calls
    queryClient.prefetchQuery({
      queryKey: ['portfolios'],
      queryFn: () => fetchJson('/api/portfolios/prices'),
      staleTime: 120_000,
    }).then(() => {
      const cached = queryClient.getQueryData<any>(['portfolios'])
      const portfolios = cached?.portfolios || []
      portfolios.forEach((p: any) => {
        const pid = p.portfolio_id || p.id
        if (!pid) return
        queryClient.setQueryData(['portfolio-prices', String(pid)], {
          holdings: p.holdings,
          total_market_value: p.total_market_value,
          total_unrealized_gain: p.total_unrealized_gain,
          total_unrealized_gain_pct: p.total_unrealized_gain_pct,
          stock_allocation_pct: p.stock_allocation_pct,
          crypto_allocation_pct: p.crypto_allocation_pct,
        })
      })
    })
  }, [])

  return null
}

export default function App() {
  return (
    <>
    <SignedIn><NotificationListener /><DataPrefetcher /></SignedIn>
    <Suspense fallback={
      <div style={{ background: '#0c0a09', minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
        <div style={{ fontSize: 28, fontFamily: 'Nunito, "Secular One", Heebo, sans-serif', fontWeight: 700, color: '#d6d3d1', letterSpacing: '-0.02em' }}>StockPro</div>
        <div style={{ width: 32, height: 32, border: '3px solid #292524', borderTopColor: '#d6d3d1', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
      </div>
    }>
    <Routes>
      {/* Root: Landing renders unconditionally so it matches the prerendered HTML
          baked in at build time (avoids React 19 hydration mismatch). When Clerk
          finishes loading and confirms a signed-in user, <SignedIn> kicks in
          and Navigate redirects to /home. Signed-out and loading states both
          show Landing. */}
      <Route
        path="/"
        element={
          <>
            <SignedIn>
              <Navigate to="/home" replace />
            </SignedIn>
            <Landing />
          </>
        }
      />

      {/* Auth pages */}
      <Route path="/sign-in/*" element={<SignIn />} />
      {/* Public signup gated behind waitlist; direct hits bounce to Landing. */}
      <Route path="/sign-up/*" element={<Navigate to="/" replace />} />

      {/* Authenticated app pages */}
      <Route
        path="/home"
        element={
          <SignedIn>
            <Home />
          </SignedIn>
        }
      />

      <Route
        path="/portfolio"
        element={
          <SignedIn>
            <PortfolioList />
          </SignedIn>
        }
      />

      <Route
        path="/portfolio/:id"
        element={
          <SignedIn>
            <PortfolioDetail />
          </SignedIn>
        }
      />

      <Route
        path="/portfolio/:id/add"
        element={
          <SignedIn>
            <AddTransaction />
          </SignedIn>
        }
      />

      <Route
        path="/portfolio/:id/import"
        element={
          <SignedIn>
            <ImportCSV />
          </SignedIn>
        }
      />

      <Route
        path="/portfolio/:id/holding/:symbol"
        element={
          <SignedIn>
            <HoldingDetail />
          </SignedIn>
        }
      />

      <Route
        path="/portfolio/:id/analytics"
        element={
          <SignedIn>
            <Analytics />
          </SignedIn>
        }
      />

      <Route
        path="/reports"
        element={
          <SignedIn>
            <ReportsHistory />
          </SignedIn>
        }
      />

      <Route
        path="/report/:id"
        element={
          <SignedIn>
            <ReportView />
          </SignedIn>
        }
      />

      <Route
        path="/chat/:reportId"
        element={
          <SignedIn>
            <Chat />
          </SignedIn>
        }
      />

      <Route
        path="/research"
        element={
          <SignedIn>
            <ResearchWizard />
          </SignedIn>
        }
      />

      <Route
        path="/watchlist"
        element={
          <SignedIn>
            <Watchlist />
          </SignedIn>
        }
      />

      <Route
        path="/alerts"
        element={
          <SignedIn>
            <Alerts />
          </SignedIn>
        }
      />

      <Route
        path="/ticker/:symbol"
        element={
          <SignedIn>
            <TickerPage />
          </SignedIn>
        }
      />

      <Route
        path="/settings"
        element={
          <>
            <SignedIn>
              <Settings />
            </SignedIn>
            <SignedOut>
              <Navigate
                to={`/sign-in?redirect_url=${encodeURIComponent(
                  typeof window !== 'undefined' ? window.location.pathname + window.location.search : '/settings'
                )}`}
                replace
              />
            </SignedOut>
          </>
        }
      />

      <Route
        path="/device"
        element={
          <>
            <SignedIn>
              <DevicePage />
            </SignedIn>
            <SignedOut>
              <Navigate
                to={`/sign-in?redirect_url=${encodeURIComponent(
                  typeof window !== 'undefined' ? window.location.pathname + window.location.search : '/device'
                )}`}
                replace
              />
            </SignedOut>
          </>
        }
      />

      <Route path="/pricing" element={<Pricing />} />

      <Route
        path="/billing/return"
        element={
          <>
            <SignedIn>
              <BillingReturn />
            </SignedIn>
            <SignedOut>
              <Navigate
                to={`/sign-in?redirect_url=${encodeURIComponent(
                  typeof window !== 'undefined' ? window.location.pathname + window.location.search : '/billing/return'
                )}`}
                replace
              />
            </SignedOut>
          </>
        }
      />

      {/* Public waitlist flow */}
      <Route path="/waitlist" element={<Waitlist />} />
      <Route path="/waitlist/thanks" element={<WaitlistThanks />} />

      {/* Public authority pages */}
      <Route path="/about" element={<About />} />
      <Route path="/press" element={<Press />} />

      {/* Public legal pages */}
      <Route path="/legal/terms" element={<Terms />} />
      <Route path="/legal/privacy" element={<Privacy />} />
      <Route path="/legal/refund" element={<Refund />} />

      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
    </>
  )
}
