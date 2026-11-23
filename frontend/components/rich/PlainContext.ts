"use client";
import { createContext } from "react";

/** True when the current agent has AG-UI components switched off: its answers are plain Markdown, and a fenced rich block is shown as ordinary code. */
export const PlainRichContext = createContext(false);
