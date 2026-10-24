import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import UploadPage from './pages/UploadPage'
import RulesPage from './pages/RulesPage'
import GraphPage from './pages/GraphPage'
import ReviewPage from './pages/ReviewPage'
import ResultsPage from './pages/ResultsPage'
import DocTypesPage from './pages/DocTypesPage'
import SettingsPage from './pages/SettingsPage'
import ForbiddenPage from './pages/ForbiddenPage'
import LoginPage from './pages/auth/LoginPage'
import RegisterPage from './pages/auth/RegisterPage'
import PendingPage from './pages/auth/PendingPage'
import ForgotPasswordPage from './pages/auth/ForgotPasswordPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import AcceptInvitePage from './pages/auth/AcceptInvitePage'
import { useAuth } from './auth/AuthContext'
import { RequireAuth, RequirePermission } from './auth/RequireAuth'
import { RuleSetProvider } from './context/RuleSetContext'
import AppLayout from './layouts/AppLayout'
import PlatformLayout from './layouts/PlatformLayout'
import RegistrationReviewPage from './pages/platform/RegistrationReviewPage'
import TenantListPage from './pages/platform/TenantListPage'

function HomeRedirect() {
  const { loading, role } = useAuth()
  if (loading) return null
  if (role === 'platform_admin') {
    return <Navigate to="/platform/registrations" replace />
  }
  if (role) return <Navigate to="/upload" replace />
  return <Navigate to="/login" replace />
}

function TenantWorkspace() {
  return (
    <RuleSetProvider>
      <Outlet />
    </RuleSetProvider>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/pending" element={<PendingPage />} />
      <Route path="/verify-email" element={<PendingPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/accept-invite" element={<AcceptInvitePage />} />
      <Route path="/forbidden" element={<ForbiddenPage />} />

      <Route element={<RequireAuth mode="tenant" />}>
        <Route element={<TenantWorkspace />}>
          <Route element={<AppLayout />}>
            <Route
              path="/upload"
              element={(
                <RequirePermission permission="review.execute">
                  <UploadPage />
                </RequirePermission>
              )}
            />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/doc-types" element={<DocTypesPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/results" element={<ResultsPage />} />
            <Route
              path="/settings"
              element={(
                <RequirePermission permission="settings.read">
                  <SettingsPage />
                </RequirePermission>
              )}
            />
          </Route>
        </Route>
      </Route>

      <Route element={<RequireAuth mode="platform" />}>
        <Route element={<PlatformLayout />}>
          <Route
            path="/platform/registrations"
            element={<RegistrationReviewPage />}
          />
          <Route
            path="/platform/tenants"
            element={<TenantListPage />}
          />
        </Route>
      </Route>

      <Route path="/" element={<HomeRedirect />} />
      <Route path="*" element={<HomeRedirect />} />
    </Routes>
  )
}

export default function App() {
  return <AppRoutes />
}
