import type { ReactNode } from "react";
import { isAllowedLink } from "../lib/safeLinks";

/** Markdown link renderer: clickable only for allowlisted destinations,
 *  otherwise the link text is shown as plain text. */
export function SafeLink({ href, children }: { href?: string; children?: ReactNode }) {
  if (!isAllowedLink(href)) return <span>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
