import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Research invitation · SixSentences_",
  description:
    "Join a consent-first research conversation in a SixSentences_ workspace.",
  openGraph: {
    title: "Research invitation · SixSentences_",
    description:
      "Join a consent-first research conversation in a SixSentences_ workspace.",
    images: ["/opengraph-image"],
  },
  twitter: { card: "summary_large_image", images: ["/opengraph-image"] },
};

export default function TalkLayout({ children }: { children: React.ReactNode }) {
  return children;
}
