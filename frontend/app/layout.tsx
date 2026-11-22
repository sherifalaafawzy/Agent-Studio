import type { Metadata } from "next";
import "./globals.css";
import Nav, { Frame, MobileBar } from "@/components/Nav";
import SideAgent from "@/components/SideAgent";
import TourRunner from "@/components/TourRunner";
import OnboardingGate from "@/components/OnboardingGate";

export const metadata: Metadata = { title: "Agent Studio", description: "Model-agnostic agent workbench" };

// Applied before first paint so the saved theme never flashes.
const THEME_BOOT = `try{var t=localStorage.getItem("as-theme");if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} /></head>
      <body>
        <div className="app">
          <MobileBar />
          <Nav />
          <main className="main"><Frame>{children}</Frame></main>
        </div>
        <SideAgent />
        <TourRunner />
        <OnboardingGate />
      </body>
    </html>
  );
}
