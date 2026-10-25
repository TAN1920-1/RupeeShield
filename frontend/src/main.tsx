import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthContext';
import './styles/theme.css';

const container = document.getElementById('root');
if (!container) {
  throw new Error('RupeeShield could not start: #root element is missing from index.html');
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      {/* Who is signed in is answered by the API, so the provider sits above every
          route — the sign-in page included, since it reads the same context. */}
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
