/**
 * Sign-in page.
 *
 * Two things it deliberately does *not* do: it never invents a session on the
 * client (the token comes only from the API), and it never pretends a demo login
 * is a normal one. The demo accounts are published by the backend, listed here
 * with their role and purpose, and the panel states plainly that they are
 * fictional.
 */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import type { DemoCredential } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Badge, Button, Card, ErrorState, Field, Loading } from '../components/ui';
import { useApiResource } from '../hooks/useApiResource';

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const { status, user, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const redirectTo = (location.state as LocationState | null)?.from;

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const credentials = useApiResource(() => api.demoCredentials(), []);
  const health = useApiResource(() => api.health(), []);

  const landing = useMemo(() => {
    if (!user) return '/';
    return user.role === 'ANALYST' ? '/overview' : '/banking';
  }, [user]);

  // Already signed in: go where this role belongs rather than showing a form that
  // cannot be used.
  useEffect(() => {
    if (status === 'authenticated' && user) {
      navigate(redirectTo && redirectTo !== '/login' ? redirectTo : landing, { replace: true });
    }
  }, [status, user, redirectTo, landing, navigate]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(email.trim(), password);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  }

  function useDemoLogin(entry: DemoCredential) {
    setEmail(entry.email);
    setPassword(entry.password);
    setError(null);
  }

  return (
    <div className="login">
      <section className="login__pitch" aria-label="About RupeeShield">
        <div className="brand brand--lg">
          <span className="brand__name">RupeeShield</span>
          <span className="brand__tagline">Real-Time Fraud Detection &amp; Anomaly Intelligence</span>
        </div>
        <h1 className="login__headline">A virtual bank with its fraud engine on the other side of the glass.</h1>
        <p className="login__lede">
          Sign in as a customer to see accounts, move money through a simulated transfer, and watch the
          risk engine react. Sign in as an analyst to run attack scenarios and investigate the incidents
          they raise — both roles read the same database, through different scopes.
        </p>
        <ul className="login__points">
          <li>Balances, transactions and incidents are real rows, not mock-ups.</li>
          <li>Every figure can be traced to a stored record.</li>
          <li>Fictional customers, invented amounts, no real account data.</li>
        </ul>
        <div className="row" style={{ gap: 8 }}>
          <span className="small dim">Backend</span>
          {health.data ? (
            <Badge tone={health.data.status === 'ok' ? 'allow' : 'block'}>{health.data.status}</Badge>
          ) : (
            <Badge tone={health.error ? 'block' : 'neutral'}>
              {health.error ? 'offline' : 'checking'}
            </Badge>
          )}
          <span className="small dim">Models</span>
          <Badge tone={health.data?.models_loaded ? 'info' : 'neutral'}>
            {health.data ? (health.data.models_loaded ? 'trained' : 'absent') : '—'}
          </Badge>
        </div>
      </section>

      <section className="login__panel" aria-label="Sign in">
        <Card>
          <h2>Sign in</h2>
          <p className="card__subtitle">Use your account, or pick a published demo login below.</p>

          <form className="stack" onSubmit={submit} noValidate>
            <Field label="Email">
              <input
                type="email"
                name="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                aria-invalid={error ? true : undefined}
              />
            </Field>

            <Field label="Password">
              <div className="password-field">
                <input
                  type={showPassword ? 'text' : 'password'}
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  aria-invalid={error ? true : undefined}
                />
                <button
                  type="button"
                  className="password-field__toggle"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-pressed={showPassword}
                >
                  {showPassword ? 'Hide' : 'Show'}
                </button>
              </div>
            </Field>

            {error && (
              <div role="alert">
                <ErrorState message={error} />
              </div>
            )}

            <Button type="submit" variant="primary" disabled={busy || !email || !password}>
              {busy ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </Card>

        <Card
          title="Demo logins"
          subtitle="Fictional accounts seeded on first start. Documented in the README, not a secret."
        >
          {credentials.loading && <Loading label="Loading demo logins…" />}
          {credentials.error && (
            <ErrorState message={credentials.error} onRetry={credentials.reload} />
          )}
          {credentials.data && credentials.data.length === 0 && (
            <p className="small dim">
              Demo mode is disabled on this deployment. Sign in with a real account instead.
            </p>
          )}
          {credentials.data && credentials.data.length > 0 && (
            <ul className="demo-list">
              {credentials.data.map((entry) => (
                <li key={entry.email} className="demo-list__item">
                  <div>
                    <div className="row" style={{ gap: 8 }}>
                      <strong>{entry.label}</strong>
                      <Badge tone={entry.role === 'ANALYST' ? 'info' : 'allow'}>{entry.role}</Badge>
                    </div>
                    <p className="small dim" style={{ margin: '4px 0 0' }}>
                      {entry.description}
                    </p>
                    <p className="mono small" style={{ margin: '4px 0 0' }}>
                      {entry.email} · {entry.password}
                    </p>
                  </div>
                  <Button size="small" onClick={() => useDemoLogin(entry)}>
                    Use
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>
    </div>
  );
}
