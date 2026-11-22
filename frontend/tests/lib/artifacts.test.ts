import { describe, expect, it } from "vitest";
import { API_BASE } from "@/lib/api";
import { agoText, artifactUrl, copyState, fmtBytes, isOurArtifactUrl, type Artifact } from "@/lib/artifacts";

const base: Artifact = { id: "0123456789ab", name: "r.md", path: "", type: "document", mime: "text/markdown", preview: "markdown", size: 10, sha256: "a".repeat(64), version: 1, created: 0, agent: "", session_id: "",
  trace_id: "", workspace: "", source: "chat", tags: [], summary: "", secrets: false, copies: [{ storage: "local", status: "synced", detail: "", size: 10, updated: 0 }] };

describe("artifact helpers", () => {
  it("writes sizes the way people read them", () => {
    expect(fmtBytes(12)).toBe("12 B");
    expect(fmtBytes(2048)).toBe("2.0 KB");
    expect(fmtBytes(20480)).toBe("20 KB");
    expect(fmtBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(fmtBytes(3 * 1024 ** 3)).toBe("3.00 GB");
  });

  it("builds content links and only trusts Agent Studio's own", () => {
    expect(artifactUrl("0123456789ab")).toBe(`${API_BASE}/api/artifacts/0123456789ab/content`);
    expect(artifactUrl("0123456789ab", true)).toBe(`${API_BASE}/api/artifacts/0123456789ab/content?download=true`);
    expect(isOurArtifactUrl(artifactUrl("0123456789ab", true))).toBe(true);
    expect(isOurArtifactUrl(artifactUrl("0123456789ab"))).toBe(true);
    expect(isOurArtifactUrl("https://evil.example/api/artifacts/0123456789ab/content")).toBe(false);
    expect(isOurArtifactUrl(`${API_BASE}/api/artifacts/../../x`)).toBe(false);
    expect(isOurArtifactUrl(`${API_BASE}/api/chat`)).toBe(false);
  });

  it("says how long ago", () => {
    expect(agoText(100, 110)).toBe("just now");
    expect(agoText(0, 300)).toBe("5 min ago");
    expect(agoText(0, 7300)).toBe("2 h ago");
    expect(agoText(0, 3 * 86400 + 5)).toBe("3 d ago");
    expect(agoText(Date.now() / 1000 + 50)).toBe("just now");
    expect(agoText(0)).toMatch(/ d ago$/);
  });

  it("sums up the state of the copies", () => {
    expect(copyState(base)).toEqual({ tone: "ok", text: "stored" });
    const two = { ...base, copies: [...base.copies, { ...base.copies[0], storage: "s3" }] };
    expect(copyState(two).text).toBe("in 2 places");
    expect(copyState({ ...two, copies: [two.copies[0], { ...two.copies[1], status: "pending" }] })).toEqual({ tone: "warn", text: "copying…" });
    expect(copyState({ ...two, copies: [two.copies[0], { ...two.copies[1], status: "blocked" }] })).toEqual({ tone: "warn", text: "1 not sent" });
    expect(copyState({ ...two, copies: [{ ...two.copies[0], status: "failed" }, { ...two.copies[1], status: "blocked" }] })).toEqual({ tone: "bad", text: "1 copy failed" });
  });
});
