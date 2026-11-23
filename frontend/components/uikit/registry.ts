// Which component draws which type. Heavy ones are loaded only when one appears (code-split), so the chat's first load is unchanged.
import { ComponentType, lazy } from "react";
import type { Spec } from "@/lib/uikit/safe";
import { CHARTS1 } from "./charts1";
import { CHARTS2 } from "./charts2";
import * as C1 from "./cards1";
import * as C2 from "./cards2";
import * as E from "./embedded";
import * as L from "./layouts";
import TableBlock from "./TableBlock";

type Draw = ComponentType<{ spec: Spec }>;

export const REGISTRY: Record<string, Draw> = {
  table: TableBlock,
  timeseries: lazy(() => import("./TimeSeriesBlock")),
  map: lazy(() => import("./MapBlock")),
  ...CHARTS1,
  ...CHARTS2,
  "card.weather": C1.Weather, "card.forecast": C1.Forecast, "card.quote": C1.Quote, "card.kpi": C1.Kpi, "card.kpis": C1.Kpis, "card.compare": C1.Compare, "card.person": C1.Person, "card.company": C1.Company,
  "card.product": C1.Product, "card.source": C1.Source, "card.results": C1.Results, "card.news": C1.News, "card.file": C1.FileCard, "card.code": C1.Code, "card.timeline": C1.Timeline, "card.event": C1.EventCard,
  "card.tasks": C1.Tasks, "card.kanban": C1.Kanban, "card.steps": C1.Steps, "card.alert": C1.Alert, "card.itinerary": C1.Itinerary, "card.place": C1.Place,
  "card.recipe": C2.Recipe, "card.receipt": C2.Receipt, "card.poll": C2.Poll, "card.definition": C2.Definition, "card.faq": C2.Faq, "card.toc": C2.Toc, "card.scorecard": C2.Scorecard, "card.countdown": C2.Countdown,
  "card.equation": C2.Equation, "card.molecule": C2.Molecule, "card.request": C2.Request, "card.log": C2.Log, "card.finding": C2.Finding, "card.usage": C2.Usage, "card.run": C2.Run, "card.approval": C2.Approval,
  "card.email": C2.Email, "card.palette": C2.Palette, "card.qr": C2.Qr, "card.sports": C2.Sports,
  "layout.stack": L.Stack, "layout.grid": L.Grid, "layout.tabs": L.Tabs, "layout.carousel": L.Carousel, "layout.split": L.Split,
  "media.audio": E.Audio, "media.video": E.Video, json: E.Json, html: E.Html,
};
