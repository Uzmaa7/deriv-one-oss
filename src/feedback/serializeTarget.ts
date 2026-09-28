export interface ElementContext {
  sectionId: string | null;
  selector: string | null;
  label: string | null;
  rect: { x: number; y: number; w: number; h: number } | null;
  viewport: { w: number; h: number; dpr: number } | null;
  tag: string | null;
}

function nearestSection(el: Element): string | null {
  let cur: Element | null = el;
  while (cur) {
    const v = cur.getAttribute("data-deriv-section");
    if (v) return v;
    cur = cur.parentElement;
  }
  return null;
}

function buildSelector(el: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
    let seg = cur.tagName.toLowerCase();
    if (cur.id) {
      seg += `#${cur.id}`;
    } else if (cur.className && typeof cur.className === "string") {
      const first = cur.className.trim().split(/\s+/)[0];
      if (first) seg += `.${first}`;
    }
    parts.unshift(seg);
    cur = cur.parentElement;
  }
  return parts.join(" > ").slice(0, 200);
}

function extractLabel(el: Element): string | null {
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel) return ariaLabel.trim().slice(0, 240);

  const placeholder = (el as HTMLInputElement).placeholder;
  if (placeholder) return placeholder.trim().slice(0, 240);

  const text = (el.textContent ?? "").trim().replace(/\s+/g, " ");
  if (text) return text.slice(0, 240);

  return null;
}

export function serializeTarget(el: Element): ElementContext {
  const r = el.getBoundingClientRect();
  return {
    sectionId: nearestSection(el),
    selector: buildSelector(el),
    label: extractLabel(el),
    rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
    viewport: { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio },
    tag: el.tagName.toLowerCase().slice(0, 32),
  };
}
