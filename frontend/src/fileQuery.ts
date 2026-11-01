import type { Api } from "./api";

import type { Query } from "@tanstack/react-query";

/**
 * A stored photo's link. A fetched link lives ten minutes, so it is reused for five. A photo sent
 * from this page is seeded with its own object URL, which stays good for the page's life.
 */
export const fileQuery = (api: Api, key: string) => ({
  queryKey: fileKey(key),
  queryFn: () => api.fileUrl(key),
  staleTime: (query: Query<string>) =>
    query.state.data?.startsWith("blob:") ? Infinity : 5 * 60_000,
});

export const fileKey = (key: string) => ["file", key];
