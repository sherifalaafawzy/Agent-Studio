import DocsApp from "@/components/docs/DocsApp";
import { DOC_PAGES } from "@/lib/docs/generated";

// The site is exported as static files, so every documentation page is listed here and written out at build time.
export const dynamicParams = false;
export function generateStaticParams() {
  return DOC_PAGES.map((p) => ({ slug: p.slug }));
}

export default async function DocsPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <DocsApp slug={slug} />;
}
