import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Autoposter — Content Distribution OS",
  description: "Create once, adapt per platform, publish with control.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
