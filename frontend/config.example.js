// Copy to public/config.js for local development; deployments generate public/config.js from
// stack outputs.
window.CONFIG = {
  cognitoDomain: "https://meters-trk-<env>-<account>.auth.eu-central-1.amazoncognito.com",
  clientId: "<user-pool-client-id>",
  apiUrl: "https://<api-id>.execute-api.eu-central-1.amazonaws.com",
  redirectUri: window.location.origin + "/",
  adminEmail: "<admin-google-address>",
};
