import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import styles from "./shell.module.css";

export const metadata: Metadata = {
  title: "Autoposter — Content Distribution OS",
  description: "Create once, adapt per platform, publish with control.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>
        <div className={styles.shell}>
          <header className={styles.header}>
            <Link className={styles.brand} href="/">
              Autoposter <span>OS</span>
            </Link>
            <nav className={styles.nav} aria-label="Основная навигация">
              <Link href="/">Composer</Link>
              <Link href="/calendar">Calendar</Link>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
