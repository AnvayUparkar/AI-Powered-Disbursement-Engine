import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppLayout } from '@/components/layout/AppLayout';
import { Skeleton } from '@/components/ui/Skeleton';
import { AuthProvider } from '@/context/AuthContext';
import { useAuth } from '@/context/auth';

const DashboardPage = lazy(() => import('@/pages/DashboardPage'));
const CasesPage = lazy(() => import('@/pages/CasesPage'));
const CaseDetailPage = lazy(() => import('@/pages/CaseDetailPage'));
const DocumentsPage = lazy(() => import('@/pages/DocumentsPage'));
const DocumentDetailPage = lazy(() => import('@/pages/DocumentDetailPage'));
const VerificationPage = lazy(() => import('@/pages/VerificationPage'));
const VerificationDetailPage = lazy(() => import('@/pages/VerificationDetailPage'));
const ReviewQueuePage = lazy(() => import('@/pages/ReviewQueuePage'));
const HumanReviewPage = lazy(() => import('@/pages/HumanReviewPage'));
const ReportsPage = lazy(() => import('@/pages/ReportsPage'));
const AuditPage = lazy(() => import('@/pages/AuditPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const LoginPage = lazy(() => import('@/pages/LoginPage'));

function PageFallback() {
  return <Skeleton className="h-96 w-full" />;
}

function AuthGate() {
  const { status } = useAuth();

  if (status === 'loading') return <div className="p-6"><PageFallback /></div>;
  if (status === 'anonymous') {
    return <Suspense fallback={<PageFallback />}><LoginPage /></Suspense>;
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<Navigate to="/documents" replace />} />
          <Route path="/dashboard" element={<Suspense fallback={<PageFallback />}><DashboardPage /></Suspense>} />
          <Route path="/cases" element={<Suspense fallback={<PageFallback />}><CasesPage /></Suspense>} />
          <Route path="/cases/:caseId" element={<Suspense fallback={<PageFallback />}><CaseDetailPage /></Suspense>} />
          <Route path="/documents" element={<Suspense fallback={<PageFallback />}><DocumentsPage /></Suspense>} />
          <Route path="/documents/:documentId" element={<Suspense fallback={<PageFallback />}><DocumentDetailPage /></Suspense>} />
          <Route path="/verification" element={<Suspense fallback={<PageFallback />}><VerificationPage /></Suspense>} />
          <Route path="/verification/:caseId" element={<Suspense fallback={<PageFallback />}><VerificationDetailPage /></Suspense>} />
          <Route path="/review" element={<Suspense fallback={<PageFallback />}><ReviewQueuePage /></Suspense>} />
          <Route path="/review/:reviewId" element={<Suspense fallback={<PageFallback />}><HumanReviewPage /></Suspense>} />
          <Route path="/reports" element={<Suspense fallback={<PageFallback />}><ReportsPage /></Suspense>} />
          <Route path="/audit" element={<Suspense fallback={<PageFallback />}><AuditPage /></Suspense>} />
          <Route path="/settings" element={<Suspense fallback={<PageFallback />}><SettingsPage /></Suspense>} />
          <Route path="*" element={<Navigate to="/documents" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AuthGate />
    </AuthProvider>
  );
}
