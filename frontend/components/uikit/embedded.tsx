"use client";
// The data views as components: audio and video from saved files, a JSON tree and a sandboxed HTML page. They reuse the viewers of the rich blocks, so a ```ui block and the
// older ```jsonview / ```html-preview / ```audio blocks look and behave the same.
import { str } from "@/lib/uikit/data";
import type { Spec } from "@/lib/uikit/safe";
import HtmlPreview from "../rich/HtmlPreview";
import JsonViewer from "../rich/JsonViewer";
import MediaBlock, { ID12 } from "../rich/MediaBlock";

type P = { spec: Spec };

const media = (kind: "audio" | "video") => function Media({ spec }: P) {
  const id = String(spec.artifact ?? "");
  if (!ID12.test(id)) throw new Error("only files saved as artifacts can be played (the 12-character id)");
  const cap = String(spec.captions ?? "");
  return <MediaBlock kind={kind} artifact={id} title={str(spec.title, 100)} captions={ID12.test(cap) ? cap : null} />;
};
export const Audio = media("audio");
export const Video = media("video");
export const Json = ({ spec }: P) => <JsonViewer value={spec.value} title={str(spec.title, 100)} />;
export const Html = ({ spec }: P) => <HtmlPreview html={str(spec.html, 200000)} title={str(spec.title, 100)} />;
