// Runtime configuration injected by public/config.js (generated per environment by
// scripts/deploy.sh), loaded as a plain script before the app bundle so a rebuild is never
// needed to retarget an environment.

export interface AppConfig {
  cognitoDomain: string;
  clientId: string;
  apiUrl: string;
  redirectUri: string;
}

declare global {
  interface Window {
    CONFIG: AppConfig;
  }
}

export function getConfig(): AppConfig {
  return window.CONFIG;
}
