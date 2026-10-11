import { Icon } from './Icon';

/**
 * Ring-and-arrow affordance for clickable cards. Hover/focus on the parent card swaps the
 * two rings and slides a fresh arrow in while the old one leaves.
 * Motion adapted from "modern-stingray-68" by xopc333 on Uiverse.io (MIT License).
 */
export function SlideArrow() {
  return <span className="slide-arrow" aria-hidden="true">
    <span className="slide-arrow-track"><Icon name="arrow" size={16}/><Icon name="arrow" size={16}/></span>
  </span>;
}
