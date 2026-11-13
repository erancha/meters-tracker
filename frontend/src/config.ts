// Runtime configuration injected by public/config.js (generated per stack pair by
// scripts/lib/app-config.sh), loaded as a plain script before the app bundle so a rebuild is never
// needed to retarget an environment.

export interface AppConfig {
  cognitoDomain: string;
  clientId: string;
  apiUrl: string;
  redirectUri: string;
  // The deployment's admin, whom a refused sign-in is offered to email.
  adminEmail: string;
}

declare global {
  interface Window {
    CONFIG: AppConfig;
  }
}

export function getConfig(): AppConfig {
  return window.CONFIG;
}
