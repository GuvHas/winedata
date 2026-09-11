import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The wine store is a local JSON file read at request time on the server.
  // Nothing is fetched from the browser, so no remote image/domain config is needed.
  typedRoutes: true,
};

export default nextConfig;
