"use client";

import { useRouter } from "next/navigation";
import { startTransition } from "react";

export default function MenuError({ reset }: { reset: () => void }) {
  const router = useRouter();
  return (
    <main className="page-shell">
      <section className="error-card" role="alert">
        <span className="error-card__icon" aria-hidden="true">!</span>
        <h1>Не удалось загрузить меню</h1>
        <p>Сервис меню сейчас недоступен. Попробуйте снова через несколько секунд.</p>
        <button className="button button--dark" onClick={() => startTransition(() => {
          router.refresh();
          reset();
        })}>Попробовать снова</button>
      </section>
    </main>
  );
}
