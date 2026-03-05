/** @type {import('next').NextConfig} */
const nextConfig = {
  // Enable PWA support
  headers: async () => {
    return [
      {
        source: "/sw.js",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=0, must-revalidate",
          },
          {
            key: "Service-Worker-Allowed",
            value: "/",
          },
        ],
      },
      {
        source: "/manifest.json",
        headers: [
          {
            key: "Content-Type",
            value: "application/manifest+json",
          },
        ],
      },
    ];
  },
  // In development, proxy /api/* and /upload-csv to the local backend to keep same-origin requests
  async rewrites() {
    if (process.env.NODE_ENV === "production") return [];
    return [
      {
        source: "/api/:path*",
        destination: `http://localhost:${process.env.BACKEND_PORT || 8080}/api/:path*`,
      },
      {
        source: "/upload-csv",
        destination: `http://localhost:${process.env.BACKEND_PORT || 8080}/upload-csv`,
      },
      {
        source: "/health",
        destination: `http://localhost:${process.env.BACKEND_PORT || 8080}/health`,
      },
    ];
  },
};

module.exports = nextConfig;
