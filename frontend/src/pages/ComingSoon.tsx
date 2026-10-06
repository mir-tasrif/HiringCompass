// Empty state for sections whose features are built in later steps.
export default function ComingSoon({ title, description }: { title: string; description: string }) {
  return (
    <>
      <h1>{title}</h1>
      <section className="card empty">
        <h2>Not available yet</h2>
        <p className="muted">{description}</p>
      </section>
    </>
  );
}