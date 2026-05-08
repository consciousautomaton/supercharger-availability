// Resolve runtime asset paths against Vite's BASE_URL so the app works both
// at domain root (Vercel / Netlify) and under a subpath (GitHub Pages, where
// site lives at https://<user>.github.io/<repo>/).
//
// BASE_URL always has a trailing slash (Vite guarantees this).

const base = import.meta.env.BASE_URL;

export function dataUrl(file: string): string {
  return `${base}data/${file}`;
}

export function textureUrl(file: string): string {
  return `${base}textures/${file}`;
}
