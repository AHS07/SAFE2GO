import { animate } from "animejs";

/** Small anime.js effects for the illustrative camera view. */

export function animateFlash(element: HTMLElement | null, color = "rgba(255, 205, 17, 0.4)"): void {
  if (!element) return;
  animate(element, {
    backgroundColor: [color, "rgba(0, 0, 0, 0)"],
    duration: 500,
    ease: "outQuad",
  });
}

export function animateReticleLock(element: HTMLElement | null): void {
  if (!element) return;
  animate(element, {
    scale: [1.3, 1],
    rotate: [0, 360],
    opacity: [0, 1],
    duration: 750,
    ease: "outBack",
  });
}
