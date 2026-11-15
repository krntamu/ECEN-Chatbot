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
// The one off-domain link the prompt allows. Matched on parsed host AND exact
// path: a prefix match would also pass /in/aarohi-mohrir-fake or
// /in/aarohi-mohrir/../../redir?url=... on the same host.
const CREATOR_HOST = "www.linkedin.com";
const CREATOR_PATH_RE = /^\/in\/aarohi-mohrir\/?$/;
// One plain address, nothing else: no second recipient, no encoded characters,
// no ?cc= / ?body= that would prefill a message the user didn't write.
const MAILTO_RE = /^[a-z0-9._+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)$/i;

function hostAllowed(host: string): boolean {
  const h = host.toLowerCase().replace(/\.$/, "");
  return ALLOWED_HOST_SUFFIXES.some(s => h === s || h.endsWith("." + s));
}

export function isAllowedLink(href: string | undefined | null): boolean {
  if (!href || href.length > 2000) return false;
  const raw = href.trim();
  // Control characters and whitespace inside a URL are only ever there to
  // confuse a parser ("java\tscript:", embedded newlines).
  if (/[\u0000-\u0020\u007f]/.test(raw)) return false;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false; // relative or malformed: nothing sensible to open
  }
  if (url.protocol === "mailto:") {
    if (url.search || url.hash) return false;
    const m = MAILTO_RE.exec(url.pathname);
    return !!m && hostAllowed(m[1]);
  }
  if (url.protocol !== "https:") return false;
  if (url.username || url.password) return false; // https://tamu.edu@evil.com
  if (url.hostname === CREATOR_HOST) {
    return CREATOR_PATH_RE.test(url.pathname) && !url.search && !url.hash && !url.port;
  }
  return hostAllowed(url.hostname);
}
