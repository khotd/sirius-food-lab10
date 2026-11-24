import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Keep the lecture demo visually clean; the dev server logs still show build status.
  devIndicators: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
