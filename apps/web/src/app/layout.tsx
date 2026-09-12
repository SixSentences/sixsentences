import type { Metadata, Viewport } from "next";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000",
  ),
  title: "SixSentences_",
  description:
    "A research workspace for literature search, source review, interviews, data analysis and manuscript writing.",
  applicationName: "SixSentences_",
  openGraph: {
    type: "website",
    siteName: "SixSentences_",
    title: "SixSentences_ · Research with every step on the record",
    description:
      "Search, inspect evidence, analyse data and write cited manuscripts in one audit-ready research workspace.",
    images: [
      {
        url: "/opengraph-image",
        width: 1200,
        height: 630,
        alt: "SixSentences_ research workspace",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "SixSentences_ · Research with every step on the record",
    description:
      "Search, inspect evidence, analyse data and write cited manuscripts in one audit-ready research workspace.",
    images: ["/opengraph-image"],
  },
  icons: {
    icon: [{ url: "/icon.svg", type: "image/svg+xml" }],
  },
  robots: {
    index: false,
    follow: false,
    noarchive: true,
    nosnippet: true,
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f1ea" },
    { media: "(prefers-color-scheme: dark)", color: "#101211" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className="antialiased"
    >
      <body className="font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
