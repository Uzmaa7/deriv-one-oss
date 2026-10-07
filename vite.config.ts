import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import crypto from 'crypto';
import fs from 'fs';
import path from 'path';
import {defineConfig, loadEnv} from 'vite';

function platformClientToken(env: Record<string, string>): string {
  const secret =
    env.PLATFORM_UI_ATTESTATION_SECRET?.trim()
    || 'dev-only-platform-ui-attestation';
  return crypto.createHmac('sha256', secret).update('platform-ui-v1').digest('hex');
}

// `@/lib/*` modules are split across two directories: the shadcn-era helpers at
// root lib/ and the newer app helpers at src/lib/. Search root first, then src,
// so a name present in both keeps resolving to root lib/.
const LIB_ROOTS = [path.resolve(__dirname, 'lib'), path.resolve(__dirname, 'src/lib')];
const LIB_EXTENSIONS = ['.ts', '.tsx', '.js', '.jsx'];

function resolveLibImport(relative: string): string | null {
  for (const base of LIB_ROOTS) {
    const target = path.resolve(base, relative);
    // Reject anything that escapes the lib directory via ../ segments.
    if (target !== base && !target.startsWith(base + path.sep)) continue;
    const candidates = [
      target,
      ...LIB_EXTENSIONS.map((ext) => target + ext),
      ...LIB_EXTENSIONS.map((ext) => path.join(target, `index${ext}`)),
    ];
    for (const candidate of candidates) {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    }
  }
  return null;
}

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  const apiPort = env.DEV_API_PORT ?? process.env.DEV_API_PORT ?? '8000';
  const apiTarget = `http://127.0.0.1:${apiPort}`;
  return {
    plugins: [react(), tailwindcss()],
    define: {
      'import.meta.env.VITE_PLATFORM_CLIENT_TOKEN': JSON.stringify(platformClientToken(env)),
    },
    resolve: {
      alias: [
        // shadcn UI components live at root components/ui
        { find: /^@\/components\/ui(.*)/, replacement: path.resolve(__dirname, 'components/ui$1') },
        // app-level components live in src/components
        { find: /^@\/components(.*)/, replacement: path.resolve(__dirname, 'src/components$1') },
        // lib helpers span root lib/ and src/lib/ — see resolveLibImport above
        { find: /^@\/lib\/(.+)$/, replacement: '$1', customResolver: (source: string) => resolveLibImport(source) },
        // app data modules live in src/data (must precede the bare '@' catch-all,
        // which points at the project root)
        { find: /^@\/data(.*)/, replacement: path.resolve(__dirname, 'src/data$1') },
        // everything else (pages, feedback, …) relative to project root
        { find: '@', replacement: path.resolve(__dirname, '.') },
      ],
    },
    build: {
      target: ['es2022', 'chrome89', 'edge89', 'firefox89', 'safari15'],
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return;
            if (id.includes('recharts') || id.includes('d3-')) return 'vendor-recharts';
            if (id.includes('@xyflow')) return 'vendor-xyflow';
            if (id.includes('@google/genai')) return 'vendor-genai';
            if (id.includes('mammoth')) return 'vendor-mammoth';
            if (id.includes('lucide-react')) return 'vendor-icons';
          },
        },
      },
    },
    optimizeDeps: {
      esbuildOptions: { target: 'es2022' },
    },
    server: {
      hmr: process.env.DISABLE_HMR !== 'true',
      // Backend jobs rewrite these on a timer (skills sync every 60s, setup
      // wizard writes platform.env). Ignore them so Vite does not full-reload
      // the SPA on every sync.
      watch: {
        ignored: [
          '**/backend/platform.env',
          '**/src/data/skills-acl.yaml',
          '**/src/data/skills/**',
        ],
      },
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        // Lab preview iframe loads /apps/{slug}/ — must hit FastAPI, not the Vite SPA.
        '/apps': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
