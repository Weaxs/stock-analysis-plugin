// Browser entry loaded by the dsh Web shell; esbuild compiles it to dist/client.js as an IIFE.
// The host injects React through `require`, so only the few members used here are typed.

type Scheme = "light" | "dark";
type Element = unknown;
type Props = Record<string, unknown> | null;

interface ReactLike {
  createElement(type: unknown, props?: Props, ...children: Element[]): Element;
  useRef<T>(initial: T): { current: T };
  useState<T>(initial: () => T): [T, (value: T) => void];
  useEffect(effect: () => void | (() => void), deps?: unknown[]): void;
}

interface ThemeSnapshot {
  active: { colorScheme: Scheme };
}

interface ThemeContext {
  theme: { getTheme(): ThemeSnapshot };
  on(event: "theme/change", listener: (snapshot: ThemeSnapshot) => void): void;
}

interface ClientContext {
  inject(deps: ["theme"], callback: (ctx: ThemeContext) => void): void;
  slots: {
    inject(name: string, callback: () => unknown): void;
    register(spec: Record<string, unknown>, component: unknown): unknown;
  };
}

declare global {
  interface Window {
    __ModuleLoader__: {
      load(module: { id: string; factory(require: (id: string) => unknown): unknown }): void;
    };
  }
}

window.__ModuleLoader__.load({
  id: "@weaxs/dsh-stock-analysis",
  factory(require) {
    const React = require("react") as ReactLike;
    const h = React.createElement;
    const PANEL = "stock-analysis";
    const SRC = "/stock-analysis/";

    function StockIcon(props?: { size?: number }) {
      const size = props?.size ?? 18;
      return h("svg", {
        width: size, height: size, viewBox: "0 0 24 24", fill: "none",
        stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round",
        "aria-hidden": true,
      },
      h("path", { d: "M4 19V9M4 7V5M4 21v-2M10 16V4M10 2V1M10 19v-3M16 18V8M16 6V4M16 21v-3M22 15V5M22 3V2M22 19v-4" }),
      h("path", { d: "M2 9h4v10H2zM8 4h4v12H8zM14 8h4v10h-4zM20 5h4v10h-4z" }));
    }

    function apply(ctx: ClientContext) {
      const frames = new Set<HTMLIFrameElement>();
      let scheme: Scheme | null = null;
      // dsh design tokens forwarded to the iframe so the page follows the host theme.
      const tokenMap: Record<string, string> = {
        bg: "--dsw-alias-bg-base",
        surface: "--dsw-alias-bg-layer-1",
        surfaceSubtle: "--dsw-alias-bg-layer-2",
        text: "--dsw-alias-label-primary",
        muted: "--dsw-alias-label-secondary",
        border: "--dsw-alias-border-l2",
        accent: "--dsw-alias-button-primary-fill",
        onAccent: "--dsw-alias-label-primary-foreground",
      };
      const palette = () => {
        const style = getComputedStyle(document.body);
        return Object.fromEntries(
          Object.entries(tokenMap)
            .map(([key, token]) => [key, style.getPropertyValue(token).trim()])
            .filter(([, value]) => value),
        );
      };
      const post = (frame: HTMLIFrameElement) => {
        if (scheme && frame.contentWindow) {
          frame.contentWindow.postMessage({ type: "stock-analysis:theme", scheme, palette: palette() }, location.origin);
        }
      };
      ctx.inject(["theme"], c => {
        const push = (snapshot: ThemeSnapshot) => {
          scheme = snapshot.active.colorScheme;
          frames.forEach(post);
        };
        push(c.theme.getTheme());
        c.on("theme/change", push);
      });

      function DashboardPanel() {
        const ref = React.useRef<HTMLIFrameElement | null>(null);
        const [src] = React.useState(() => (scheme ? `${SRC}?theme=${scheme}` : SRC));
        React.useEffect(() => {
          const frame = ref.current;
          if (frame) frames.add(frame);
          return () => {
            if (frame) frames.delete(frame);
          };
        }, []);
        return h("iframe", {
          ref, src, title: "股票仪表盘", allow: "fullscreen", referrerPolicy: "no-referrer",
          onLoad: (event: { currentTarget: HTMLIFrameElement }) => post(event.currentTarget),
          style: { width: "100%", height: "100%", border: 0, display: "block", background: "Canvas" },
        });
      }

      ctx.slots.inject("sidebar.panellist", () =>
        ctx.slots.register({ name: "sidebar.panellist", id: PANEL, order: 7, label: "股票仪表盘" }, StockIcon));
      ctx.slots.inject("main", () => ctx.slots.register({ name: "main", key: PANEL }, DashboardPanel));
    }

    return { isPlugin: true, inject: ["slots"], apply };
  },
});

export {};
