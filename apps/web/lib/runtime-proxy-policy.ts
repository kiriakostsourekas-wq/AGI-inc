export function mutationRequestIsSameOrigin(
  headers: Pick<Headers, "get">,
  requestUrl: string,
): boolean {
  if (headers.get("X-Trust-CSRF") !== "1") return false;
  const expectedOrigin = new URL(requestUrl).origin;
  const origin = headers.get("Origin");
  if (origin && origin !== expectedOrigin) return false;
  const fetchSite = headers.get("Sec-Fetch-Site");
  if (fetchSite && fetchSite !== "same-origin") return false;
  return true;
}
