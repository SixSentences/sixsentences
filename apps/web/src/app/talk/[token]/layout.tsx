import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Research invitation · SixSentences_",
  description:
    "Join a consent-first research conversation hosted securely with SixSentences_.",
  openGraph: {
    title: "Research invitation · SixSentences_",
    description:
      "Join a consent-first research conversation hosted securely with SixSentences_.",
    images: ["/opengraph-image"],
  },
  twitter: { card: "summary_large_image", images: ["/opengraph-image"] },
};

export default function TalkLayout({ children }: { children: React.ReactNode }) {
  return children;
}
