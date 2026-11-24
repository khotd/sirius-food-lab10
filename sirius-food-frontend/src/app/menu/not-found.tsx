import Link from "next/link";

export default function DishNotFound() {
  return (
    <main className="page-shell">
      <section className="empty-state">
        <h1>Блюдо не найдено</h1>
        <p>Возможно, его больше нет в меню.</p>
        <Link className="button button--dark" href="/menu">Вернуться к меню</Link>
      </section>
    </main>
  );
}
