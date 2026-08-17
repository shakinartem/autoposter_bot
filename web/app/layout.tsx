import type { Metadata } from "next";
import Link from "next/link";
import { SessionMenu } from "@/components/session-menu";
import "./globals.css";
import "./media.css";
import styles from "./shell.module.css";

export const metadata: Metadata = {
  title: "Autoposter — Content Distribution OS",
  description: "Create once, adapt per platform, publish with control.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const sessionMode = (process.env.AUTOPOSTER_WEB_AUTH_MODE ?? "service").trim().toLowerCase() === "session";

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
              <Link href="/accounts">Accounts</Link>
            </nav>
            {sessionMode ? <SessionMenu /> : null}
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
