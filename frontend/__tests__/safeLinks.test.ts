import { isAllowedLink } from "../lib/safeLinks";
import { clientIpFromXff } from "../lib/clientIp";

describe("isAllowedLink", () => {
  it.each([
    "https://engineering.tamu.edu/electrical/index.html",
    "https://tamu.edu",
    "https://calendar.tamu.edu/ecen/",
    "mailto:ecen-advising@tamu.edu",
    "mailto:someone@ece.tamu.edu",
    "https://www.linkedin.com/in/aarohi-mohrir/",
  ])("allows %s", href => expect(isAllowedLink(href)).toBe(true));

  it.each([
    "https://evil.example.com",
    "http://engineering.tamu.edu/",            // not https
    "https://tamu.edu.evil.com/",              // suffix trick
    "https://nottamu.edu/",                    // no dot boundary
    "https://tamu.edu@evil.com/",              // userinfo trick
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "mailto:someone@gmail.com",
    "mailto:a@tamu.edu,b@evil.com",
    "https://www.linkedin.com/in/someone-else/",
    "/relative/path",
    "",
  ])("blocks %s", href => expect(isAllowedLink(href)).toBe(false));

  it("blocks missing hrefs", () => {
    expect(isAllowedLink(undefined)).toBe(false);
    expect(isAllowedLink(null)).toBe(false);
  });
});

describe("clientIpFromXff", () => {
  it("ignores a client-supplied leading entry (Cloud Run appends the real IP)", () => {
    expect(clientIpFromXff("1.2.3.4, 203.0.113.9", 1)).toBe("203.0.113.9");
  });
  it("returns the only entry when there is one", () => {
    expect(clientIpFromXff("203.0.113.9", 1)).toBe("203.0.113.9");
  });
  it("counts back past a load balancer when there are two trusted hops", () => {
    expect(clientIpFromXff("spoofed, 203.0.113.9, 35.191.0.1", 2)).toBe("203.0.113.9");
  });
  it("does not run off the front of a short list", () => {
    expect(clientIpFromXff("203.0.113.9", 2)).toBe("203.0.113.9");
  });
  it("falls back when the header is absent", () => {
    expect(clientIpFromXff(null)).toBe("unknown");
    expect(clientIpFromXff("")).toBe("unknown");
  });
});
