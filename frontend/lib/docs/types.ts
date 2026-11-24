// The shape of the generated documentation (see scripts/build-docs.mjs).
export type DocSection = { id: string; level: 2 | 3; heading: string; body: string };
export type DocPage = {
  slug: string; title: string; section: string; order: number; summary: string;
  routes: string[]; tags: string[]; intro: string; sections: DocSection[]; shots: string[]; words: number;
};
export type DocShot = { id: string; alt: string; file: string; mobile: boolean };
