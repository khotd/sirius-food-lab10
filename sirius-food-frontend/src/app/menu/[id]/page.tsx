import Link from "next/link";
import { notFound } from "next/navigation";
import { AddToCartButton } from "@/components/AddToCartButton";
import { DishArtwork } from "@/components/DishArtwork";
import { formatPrice, getMenuItem } from "@/lib/api";
import { presentDish } from "@/lib/presentation";

export default async function MenuItemPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const item = await getMenuItem(id);
  if (!item) notFound();

  const category = item.category ?? "Меню";
  const available = item.is_available !== false;
  return (
    <main className="page-shell detail-page">
      <nav className="breadcrumbs" aria-label="Путь">
        <Link href="/menu">Меню</Link><span aria-hidden="true">/</span><span>Блюдо #{item.id}</span>
      </nav>
      <article className="detail-card">
        <div className="detail-card__art"><DishArtwork {...presentDish(item)} category={category} /></div>
        <div className="detail-card__info">
          <p className="eyebrow">{category}</p>
          <h1>{item.name}</h1>
          <p className="detail-card__description">{available ? "Блюдо доступно к заказу." : "Сейчас блюдо недоступно."}</p>
          <div className="detail-price-row">
            <div><span className="detail-price-label">Цена за порцию</span><strong className="detail-price">{formatPrice(item.price)}</strong></div>
          </div>
          <AddToCartButton available={available} />
        </div>
      </article>
    </main>
  );
}
