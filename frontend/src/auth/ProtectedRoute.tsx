/**
 * Route guards.
 *
 * These control what is *rendered*, not what is *permitted*: the API re-checks the
 * role and the row ownership on every request, so a user who types a URL by hand
 * still receives a 403 from the server. The guard exists so the interface does not
 * offer a door that will not open.
 */
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { Card, Loading, Notice, PageHeader } from '../components/ui';
import { useAuth } from './AuthContext';
import type { UserRole } from '../api/types';

export function ProtectedRoute({
  children,
  role,
}: {
  children: ReactNode;
  role?: UserRole;
}) {
  const { status, user } = useAuth();
  const location = useLocation();

  if (status === 'checking') {
    return (
      <Card>
        <Loading label="Restoring your session…" />
      </Card>
    );
  }

  if (status !== 'authenticated' || !user) {
    // Preserve where the user was heading so a successful sign-in can return there.
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  if (role && user.role !== role) {
    return (
      <>
        <PageHeader
          title="Not available for this role"
          description="This area is part of the security-operations console, which is restricted to the analyst role."
        />
        <Card title="Access denied">
          <Notice tone="warning">
            You are signed in as a {user.role.toLowerCase()}. The backend enforces this restriction,
            so the request would be refused even if the page rendered. Sign in with the demo analyst
            account to use the security workspace.
          </Notice>
        </Card>
      </>
    );
  }

  return <>{children}</>;
}
