import { defineConfig } from "vite";

// `base` makes asset URLs resolve under the GitHub Pages repo subpath
// (https://<user>.github.io/<repo>/). Override with VITE_BASE for other
// hosts that serve at the domain root.
const base = process.env.VITE_BASE ?? "/supercharger-availability/";

export default defineConfig({
  base,
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
});
