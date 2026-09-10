/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Do not reuse the dynamic router cache across client navigations, so a query-only
  // change (e.g. ?audit=<id>) re-renders the server component that reads searchParams.
  // Matches Next 15's default; without it, clicking a citation would not update the panel.
  experimental: { staleTimes: { dynamic: 0 } },
};
export default nextConfig;
