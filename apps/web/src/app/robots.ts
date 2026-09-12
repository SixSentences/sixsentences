import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    // Crawlers must be able to fetch app pages in order to see the noindex
    // response header and metadata. Blocking them here could preserve stale
    // URLs in search results.
    rules: {
      userAgent: "*",
      allow: "/",
    },
  };
}
