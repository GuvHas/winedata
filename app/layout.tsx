import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Vinbetyg — Munskänkarnas viner på Systembolaget',
  description:
    'Veckans vinbetyg från Munskänkarna, matchade mot Systembolagets sortiment. ' +
    'Filtrera på typ, pris och prisvärdhet, och gå direkt till produkten.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // Never block zoom — pinching is a basic accessibility affordance.
  maximumScale: 5,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fdfcfa' },
    { media: '(prefers-color-scheme: dark)', color: '#1c1a19' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="sv">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
