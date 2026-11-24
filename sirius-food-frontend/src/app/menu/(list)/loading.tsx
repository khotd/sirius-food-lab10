export default function MenuLoading() {
  return (
    <main className="page-shell loading-page" role="status" aria-busy="true">
      <span className="visually-hidden">Загружаем меню</span>
      <div className="loading-hero shimmer" aria-hidden="true" />
      <div className="loading-title shimmer" aria-hidden="true" />
      <div className="loading-grid" aria-hidden="true">
        {Array.from({ length: 6 }, (_, index) => (
          <div className="loading-card" key={index}>
            <div className="loading-card__art shimmer" />
            <div className="loading-card__line shimmer" />
            <div className="loading-card__line loading-card__line--short shimmer" />
          </div>
        ))}
      </div>
    </main>
  );
}
