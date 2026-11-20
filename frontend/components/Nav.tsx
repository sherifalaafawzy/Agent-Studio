"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  ["/", "Chat"], ["/external/", "External agents"], ["/agents/", "Agent Studio"], ["/billing/", "Billing"], ["/settings/", "Settings"],
] as const;

export default function Nav() {
  const path = usePathname() ?? "/";
  return (
    <nav className="nav">
      <h1>◆ Agent Studio</h1>
      {items.map(([href, label]) => (
        <Link key={href} href={href} className={path === href ? "on" : ""}>
          {label}
        </Link>
      ))}
    </nav>
  );
}
