import Link from "next/link";
import styles from "./login.module.css";

type LoginPageProps = {
  searchParams: Promise<{ error?: string | string[] }>;
};

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const error = Array.isArray(params.error) ? params.error[0] : params.error;

  return (
    <main className={styles.page}>
      <section className={styles.card}>
        <div className={styles.mark}>A</div>
        <p className={styles.eyebrow}>AUTPOSTER WORKSPACE</p>
        <h1>Войти в Content OS</h1>
        <p className={styles.copy}>
          Telegram подтверждает вашу личность. После входа сервер создаёт revocable workspace session и сохраняет её только в HttpOnly cookie.
        </p>
        {error ? <div className={styles.error}>{error}</div> : null}
        <Link className={styles.telegram} href="/auth/telegram?return_path=/">
          Войти через Telegram
        </Link>
        <p className={styles.security}>
          Пароль не создаётся. Токены Telegram и Autoposter session не доступны React-коду страницы.
        </p>
      </section>
    </main>
  );
}
