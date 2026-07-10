import { describe, expect, it } from "vitest";

import { mutationRequestIsSameOrigin } from "./runtime-proxy-policy";

describe("runtime proxy mutation policy", () => {
  it("requires the custom CSRF marker and rejects cross-origin browser requests", () => {
    const good = new Headers({
      "X-Trust-CSRF": "1",
      Origin: "https://console.example",
      "Sec-Fetch-Site": "same-origin",
    });
    const foreign = new Headers({
      "X-Trust-CSRF": "1",
      Origin: "https://attacker.example",
      "Sec-Fetch-Site": "cross-site",
    });
    const missing = new Headers({ Origin: "https://console.example" });
    expect(mutationRequestIsSameOrigin(good, "https://console.example/api/runtime/v1/runs")).toBe(
      true,
    );
    expect(
      mutationRequestIsSameOrigin(foreign, "https://console.example/api/runtime/v1/runs"),
    ).toBe(false);
    expect(
      mutationRequestIsSameOrigin(missing, "https://console.example/api/runtime/v1/runs"),
    ).toBe(false);
  });
});
