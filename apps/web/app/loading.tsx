export default function Loading() {
  return (
    <div className="page-shell route-loading" aria-live="polite" aria-busy="true">
      <span className="route-loading__mark" aria-hidden="true" />
      <p>Loading the evidence surface…</p>
    </div>
  );
}
