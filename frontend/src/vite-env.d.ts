// The build's version, "MAJOR.MINOR.COMMITS" — the release tag the checkout descends from and the
// commits since — set by vite.config.ts when the app is built, served or tested.
declare const __APP_VERSION__: string;

// The date of the build's last commit, DD/MM/YYYY, set alongside the version.
declare const __APP_COMMIT_DATE__: string;
