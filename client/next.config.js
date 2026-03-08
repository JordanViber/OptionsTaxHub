/** @type {import('next').NextConfig} */
function localDestination(path) {
  return `http://localhost:${process.env.BACKEND_PORT || 8011}${path}`;
}

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
        destination: localDestination("/api/:path*"),
      },
      {
        source: "/upload-csv",
        destination: localDestination("/upload-csv"),
      },
      {
        source: "/health",
        destination: localDestination("/health"),
      },
      {
        source: "/push/:path*",
        destination: localDestination("/push/:path*"),
      },
    ];
  },
};

module.exports = nextConfig;
