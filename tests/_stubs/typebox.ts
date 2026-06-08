const g = globalThis as any;
export const Type = new Proxy(
  {},
  {
    get(_target, prop: string) {
      return (...args: unknown[]) =>
        g.__OPENCLAW_TEST__.fakeTypebox.Type[prop](...args);
    },
  }
);
