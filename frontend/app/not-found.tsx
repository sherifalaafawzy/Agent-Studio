import Link from "next/link";

// A wrong address used to show the framework's bare white "404" page: no way back, and blinding in the dark theme.
export default function NotFound() {
  return (
    <div className="notfound" data-testid="not-found">
      <div className="notfound-code" aria-hidden>404</div>
      <h2>This page does not exist</h2>
      <p className="page-sub">The address may be mistyped, or the page was moved. Nothing was lost: your chats, agents and settings are where you left them.</p>
      <div className="row" style={{ justifyContent: "center" }}>
        <Link href="/" className="btn acc">Go to the chat</Link>
        <Link href="/dashboard/" className="btn">Open the dashboard</Link>
        <Link href="/docs/" className="btn">Read the documentation</Link>
      </div>
    </div>
  );
}
