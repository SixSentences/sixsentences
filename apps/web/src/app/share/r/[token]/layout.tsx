import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Shared research record · SixSentences_",
  description:
    "Open a read-only, audit-ready research record shared from SixSentences_.",
  openGraph: {
    title: "Shared research record · SixSentences_",
    description:
      "Open a read-only, audit-ready research record shared from SixSentences_.",
    images: ["/opengraph-image"],
  },
  twitter: { card: "summary_large_image", images: ["/opengraph-image"] },
};

export default function SharedRunLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
