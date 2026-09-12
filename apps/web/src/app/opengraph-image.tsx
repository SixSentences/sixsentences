import { ImageResponse } from "next/og";

export const alt = "SixSentences_ research workspace";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

function Mark() {
  const widths = [84, 66, 78, 54, 72, 30];
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        width: 92,
      }}
    >
      {widths.map((width, index) => (
        <div key={width} style={{ display: "flex", gap: 8 }}>
          <div
            style={{
              background: "#f4f1ea",
              borderRadius: 6,
              display: "flex",
              height: 8,
              width,
            }}
          />
          {index === widths.length - 1 ? (
            <div
              style={{
                background: "#91b49e",
                borderRadius: 2,
                display: "flex",
                height: 8,
                width: 13,
              }}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "#f4f1ea",
          color: "#0c1d19",
          display: "flex",
          height: "100%",
          padding: 42,
          width: "100%",
        }}
      >
        <div
          style={{
            background:
              "radial-gradient(circle at 82% 15%, #7ca98f 0, transparent 34%), radial-gradient(circle at 14% 88%, #b8cfae 0, transparent 38%), #0c1d19",
            borderRadius: 32,
            color: "#f4f1ea",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            padding: "54px 64px 60px",
            width: "100%",
          }}
        >
          <div
            style={{
              alignItems: "center",
              display: "flex",
              justifyContent: "space-between",
            }}
          >
            <Mark />
            <div
              style={{
                color: "#c7d7cf",
                display: "flex",
                fontFamily: "monospace",
                fontSize: 20,
                letterSpacing: "0.18em",
              }}
            >
              SIXSENTENCES_
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", maxWidth: 940 }}>
            <div
              style={{
                display: "flex",
                fontFamily: "serif",
                fontSize: 73,
                letterSpacing: "-0.035em",
                lineHeight: 1.02,
              }}
            >
              Research with every step on the record.
            </div>
            <div
              style={{
                color: "#c9d8cf",
                display: "flex",
                fontFamily: "sans-serif",
                fontSize: 25,
                lineHeight: 1.35,
                marginTop: 27,
              }}
            >
              Search · evidence · data · interviews · manuscript
            </div>
          </div>
        </div>
      </div>
    ),
    size,
  );
}
