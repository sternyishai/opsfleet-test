import type { Item } from "../showcase";

/** A page of design material: the diagrams (open full size on click) or the films. */
export function Showcase(props: { kind: "diagrams" | "films"; title: string; intro: string; items: Item[] }) {
  return (
    <div className="showcase">
      <header className="showcase-head">
        <h1>{props.title}</h1>
        <p className="muted">{props.intro}</p>
      </header>
      <div className={`showcase-grid ${props.kind}`}>
        {props.items.map((item) => (
          <figure key={item.src} className="showcase-card">
            {props.kind === "films" ? (
              <video src={item.src} poster={item.poster} controls preload="metadata" />
            ) : (
              <a href={item.src} target="_blank" rel="noreferrer" title="Open full size">
                <img src={item.src} alt={item.title} loading="lazy" />
              </a>
            )}
            <figcaption>
              <div className="showcase-title">
                <span>{item.title}</span>
                <span className="eyebrow">{item.source}</span>
              </div>
              <p className="muted">{item.caption}</p>
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
