import { useCallback, useEffect, useRef, useState } from "react";
import { useFeedback } from "./FeedbackProvider";

interface HighlightBox {
  top: number;
  left: number;
  width: number;
  height: number;
}

export default function PickerOverlay() {
  const { setPicked, toggleAnnotate } = useFeedback();
  const [highlight, setHighlight] = useState<HighlightBox | null>(null);
  const hoveredRef = useRef<Element | null>(null);
  // Path from original hovered element to current promoted ancestor
  const pathRef = useRef<Element[]>([]);
  const pathIndexRef = useRef(0);

  const applyHighlight = (el: Element) => {
    const r = el.getBoundingClientRect();
    setHighlight({ top: r.top, left: r.left, width: r.width, height: r.height });
  };

  const handleMouseMove = useCallback((e: MouseEvent) => {
    const el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el || el === document.documentElement || el === document.body) return;
    hoveredRef.current = el;
    pathRef.current = [el];
    pathIndexRef.current = 0;
    applyHighlight(el);
  }, []);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === "Escape") {
      toggleAnnotate();
      return;
    }
    if (e.altKey && e.key === "ArrowUp") {
      e.preventDefault();
      const current = pathRef.current[pathIndexRef.current];
      if (current?.parentElement) {
        pathRef.current = [...pathRef.current, current.parentElement];
        pathIndexRef.current++;
        applyHighlight(current.parentElement);
      }
      return;
    }
    if (e.altKey && e.key === "ArrowDown") {
      e.preventDefault();
      if (pathIndexRef.current > 0) {
        pathIndexRef.current--;
        applyHighlight(pathRef.current[pathIndexRef.current]);
      }
    }
  }, [toggleAnnotate]);

  const handleClick = useCallback((e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const target = pathRef.current[pathIndexRef.current] ?? hoveredRef.current;
    if (target) setPicked(target);
  }, [setPicked]);

  useEffect(() => {
    document.addEventListener("mousemove", handleMouseMove, true);
    document.addEventListener("keydown", handleKeyDown, true);
    document.addEventListener("click", handleClick, true);
    document.body.style.cursor = "crosshair";
    return () => {
      document.removeEventListener("mousemove", handleMouseMove, true);
      document.removeEventListener("keydown", handleKeyDown, true);
      document.removeEventListener("click", handleClick, true);
      document.body.style.cursor = "";
    };
  }, [handleMouseMove, handleKeyDown, handleClick]);

  return (
    <>
      {/* Invisible overlay to intercept events visually but pointer-events:none so mousemove still reaches real elements */}
      <div
        style={{ position: "fixed", inset: 0, zIndex: 9999, pointerEvents: "none" }}
        aria-hidden="true"
      />
      {highlight && (
        <div
          aria-hidden="true"
          style={{
            position: "fixed",
            top: highlight.top,
            left: highlight.left,
            width: highlight.width,
            height: highlight.height,
            outline: "2px solid #3b82f6",
            outlineOffset: "2px",
            backgroundColor: "rgba(59,130,246,0.08)",
            zIndex: 9999,
            pointerEvents: "none",
            borderRadius: "3px",
            transition: "top 60ms,left 60ms,width 60ms,height 60ms",
          }}
        />
      )}
    </>
  );
}
