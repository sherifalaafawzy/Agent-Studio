"use client";
import { createContext } from "react";

/** The chat provides this so a form, poll, checklist or approval card in an answer can send the user's reply as their next message. */
export const ReplyContext = createContext<((text: string) => void) | null>(null);
