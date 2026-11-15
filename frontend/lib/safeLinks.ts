/**
 * Which links in a model-written answer are allowed to be clickable.
 *
 * The system prompt tells the model not to put URLs in answers, but a prompt
 * is a request, not a control: a crawled page or a crafted question could
 * still get a link into the output. Rendering only links to the university's
 * own domains means the worst case is a dead-looking piece of text, not a
 * department-branded assistant sending a student to an arbitrary site.
 *
 * Allowed: https links on tamu.edu (and subdomains), mailto: to tamu.edu
 * addresses, and the one deliberate exception in the prompt, the creator's
 * LinkedIn profile.
 */
const ALLOWED_HOST_SUFFIXES = ["tamu.edu"];
const ALLOWED_EXACT_PREFIXES = ["https://www.linkedin.com/in/aarohi-mohrir"];

function hostAllowed(host: string): boolean {
  const h = host.toLowerCase().replace(/\.$/, "");
  return ALLOWED_HOST_SUFFIXES.some(s => h === s || h.endsWith("." + s));
}

export function isAllowedLink(href: string | undefined | null): boolean {
  if (!href) return false;
  const raw = href.trim();
  if (ALLOWED_EXACT_PREFIXES.some(p => raw.startsWith(p))) return true;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false; // relative or malformed: nothing sensible to open
  }
  if (url.protocol === "mailto:") {
    const addr = decodeURIComponent(url.pathname);
    const at = addr.lastIndexOf("@");
    return at > 0 && !addr.includes(",") && hostAllowed(addr.slice(at + 1));
  }
  if (url.protocol !== "https:") return false;
  if (url.username || url.password) return false; // https://tamu.edu@evil.com
  return hostAllowed(url.hostname);
}
