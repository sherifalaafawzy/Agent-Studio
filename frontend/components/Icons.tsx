// Minimal stroke icons (24px grid, currentColor).
type P = { size?: number; className?: string };
const S = ({ size = 18, className, children }: P & { children: React.ReactNode }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
    strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">{children}</svg>
);

export const IChat = (p: P) => <S {...p}><path d="M21 12a8 8 0 0 1-11.7 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" /></S>;
export const IPlug = (p: P) => <S {...p}><path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7ZM12 16v5" /></S>;
export const IAgents = (p: P) => <S {...p}><circle cx="12" cy="6" r="2.5" /><circle cx="5.5" cy="17" r="2.5" /><circle cx="18.5" cy="17" r="2.5" /><path d="M12 8.5v3M12 11.5l-5 3.5M12 11.5l5 3.5" /></S>;
export const IPulse = (p: P) => <S {...p}><path d="M3 12h4l2.5-6 4 12 2.5-6H21" /></S>;
export const ICoin = (p: P) => <S {...p}><ellipse cx="12" cy="7" rx="7" ry="3" /><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" /></S>;
export const IGear = (p: P) => <S {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" /></S>;
export const IPlus = (p: P) => <S {...p}><path d="M12 5v14M5 12h14" /></S>;
export const ISide = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" /></S>;
export const IArrowUp = (p: P) => <S {...p}><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></S>;
export const IStop = (p: P) => <svg width={p.size ?? 14} height={p.size ?? 14} viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2.5" fill="currentColor" /></svg>;
export const ICopy = (p: P) => <S {...p}><rect x="9" y="9" width="11" height="11" rx="2.5" /><path d="M5 15V6a2 2 0 0 1 2-2h8" /></S>;
export const ICheck = (p: P) => <S {...p}><path d="M5 12.5 10 17 19 7.5" /></S>;
export const IX = (p: P) => <S {...p}><path d="M6 6l12 12M18 6 6 18" /></S>;
export const IKnowledge = (p: P) => <S {...p}><path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" /><path d="m3 12 9 4.5 9-4.5" /><path d="m3 16.5 9 4.5 9-4.5" /></S>;
export const ISetup = (p: P) => <S {...p}><rect x="4" y="3.5" width="16" height="17" rx="3" /><path d="m8 9 1.6 1.6L12 8M8 15l1.6 1.6L12 14M14.5 9.5H17M14.5 15.5H17" /></S>;
export const IHelp = (p: P) => <S {...p}><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7v.5" /><path d="M12 17h.01" /></S>;
export const ITool = (p: P) => <S {...p}><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4 2.6-2.6Z" /></S>;
export const IChevron = (p: P) => <S {...p}><path d="m9 6 6 6-6 6" /></S>;
export const ISliders = (p: P) => <S {...p}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></S>;
export const ISun = (p: P) => <S {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></S>;
export const IMoon = (p: P) => <S {...p}><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" /></S>;
export const IMonitor = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></S>;
export const IFolder = (p: P) => <S {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" /></S>;
export const ISave = (p: P) => <S {...p}><path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4Z" /><path d="M8 4v5h7V4M8 20v-6h8v6" /></S>;

export const ILock = (p: P) => <S {...p}><rect x="5" y="10.5" width="14" height="10" rx="2.5" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2" /></S>;
export const IFlask = (p: P) => <S {...p}><path d="M9 3h6M10 3v6.5L4.6 18.2A2 2 0 0 0 6.3 21h11.4a2 2 0 0 0 1.7-2.8L14 9.5V3" /><path d="M7.5 15h9" /></S>;
export const IBook = (p: P) => <S {...p}><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5Z" /><path d="M4 19a2 2 0 0 1 2-2h13" /><path d="M9 7h6" /></S>;
export const IServer = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="7" rx="2" /><rect x="3" y="13" width="18" height="7" rx="2" /><path d="M7 7.5h.01M7 16.5h.01" /></S>;
export const ISearch = (p: P) => <S {...p}><circle cx="11" cy="11" r="6.5" /><path d="m20 20-4-4" /></S>;
export const IPlay = (p: P) => <S {...p}><path d="M7 5v14l11-7L7 5Z" /></S>;
export const ITrash = (p: P) => <S {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></S>;
export const IEdit = (p: P) => <S {...p}><path d="M4 20h4L19 9l-4-4L4 16v4Z" /></S>;
export const IKey = (p: P) => <S {...p}><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M17 6l3 3" /></S>;
export const IShield = (p: P) => <S {...p}><path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3Z" /></S>;

export const ITimeline = (p: P) => <S {...p}><path d="M4 6h9M8 12h12M4 18h7" /><circle cx="15.5" cy="6" r="1.6" /><circle cx="5.5" cy="12" r="1.6" /><circle cx="13.5" cy="18" r="1.6" /></S>;

export const IGrid = (p: P) => <S {...p}><rect x="3" y="3" width="7.5" height="9" rx="2" /><rect x="13.5" y="3" width="7.5" height="5" rx="2" /><rect x="13.5" y="11" width="7.5" height="10" rx="2" /><rect x="3" y="15" width="7.5" height="6" rx="2" /></S>;

export const IBrain = (p: P) => <S {...p}><path d="M9 4.5a3 3 0 0 0-3 3v.2A3 3 0 0 0 4.5 13a3 3 0 0 0 1.6 2.6A3 3 0 0 0 9 19.5a3 3 0 0 0 3-3v-12a3 3 0 0 0-3-3Z" /><path d="M15 4.5a3 3 0 0 1 3 3v.2a3 3 0 0 1 1.5 5.3 3 3 0 0 1-1.6 2.6 3 3 0 0 1-3.4 3.4 3 3 0 0 1-3-3" /></S>;

/** Agent Studio's own mark: a faceted spark. */
export const Spark = ({ size = 18, className }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden="true" fill="currentColor">
    <path d="M12 1.8l1.9 6.4 6.3-2.4-4.1 5.4 5.7 3.6-6.7.5.9 6.6L12 16.6l-3.9 5.3.9-6.6-6.7-.5 5.7-3.6-4.1-5.4 6.3 2.4L12 1.8Z" />
  </svg>
);
export const ICatalog = (p: P) => <S {...p}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><path d="M14 17.5h7M17.5 14v7" /></S>;
export const IFlow = (p: P) => <S {...p}><rect x="3" y="3" width="6" height="5" rx="1.2" /><rect x="15" y="3" width="6" height="5" rx="1.2" /><rect x="9" y="16" width="6" height="5" rx="1.2" /><path d="M6 8v3h12V8M12 11v5" /></S>;
export const IFile = (p: P) => <S {...p}><path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" /><path d="M14 3v4h4M9 12h6M9 16h6" /></S>;
export const IDownload = (p: P) => <S {...p}><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 20h14" /></S>;
export const IArchive = (p: P) => <S {...p}><rect x="3" y="4" width="18" height="5" rx="1.5" /><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4" /></S>;
