import type { Metadata } from "next";
import { DishArtwork } from "@/components/DishArtwork";
import { DishCard } from "@/components/DishCard";
import { getMenuItems } from "@/lib/api";

export const metadata: Metadata = { title: "Меню" };

export default async function MenuPage() {
  const items = await getMenuItems();
  return (
    <main className="page-shell">
      <section className="hero-panel">
        <div className="hero-copy">
          <p className="eyebrow eyebrow--light"><span className="eyebrow-dot" /> Столовая кампуса</p>
          <h1>Меню <em>Сириус.Еды</em></h1>
          <p className="hero-description">Горячие блюда, завтраки и напитки. Откройте карточку, чтобы узнать цену и наличие.</p>
          <div className="hero-actions">
            <a className="button button--lime" href="#menu">Посмотреть блюда <span aria-hidden="true">↓</span></a>
          </div>
        </div>
        <div className="hero-visual" aria-hidden="true">
          <div className="hero-visual__halo" />
          <div className="hero-visual__dish"><DishArtwork emoji="🍽️" tone="gold" category="Меню" /></div>
        </div>
      </section>
      <section className="menu-section" id="menu" aria-labelledby="catalog-title">
        <div className="section-heading menu-catalog-heading">
          <div>
            <h2 id="catalog-title">Сегодня в меню<span className="heading-period">.</span></h2>
            <p className="section-description">Наличие и цены по данным столовой.</p>
          </div>
          <div className="menu-count"><strong>{items.length}</strong><span>Позиций<br />в меню</span></div>
        </div>
        {items.length ? (
          <div className="dish-grid">{items.map((item) => <DishCard key={item.id} item={item} />)}</div>
        ) : (
          <div className="empty-state"><h3>Меню пока пусто</h3><p>Загляните позже.</p></div>
        )}
      </section>
    </main>
  );
}
