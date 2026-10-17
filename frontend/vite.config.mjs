import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv } from 'vite';

const frontendDir = fileURLToPath(new URL('.', import.meta.url));
const backendDir = fileURLToPath(new URL('../backend/', import.meta.url));
const loopbackAddresses = new Set(['127.0.0.1', '::1', '::ffff:127.0.0.1']);

export function localApiGuard(req, res, next) {
  if (!req.url?.startsWith('/api')) return next();
  const host = req.headers.host || '';
  const origin = req.headers.origin;
  // The proxy attaches credentials, so reject remote clients and cross-origin requests.
  if (
    !loopbackAddresses.has(req.socket.remoteAddress) ||
    !/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/i.test(host) ||
    (origin && origin !== `http://${host}`)
  ) {
    res.statusCode = 403;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ detail: 'Local API requests must come from this app on localhost.' }));
    return;
  }
  next();
}

export default defineConfig(({ command, mode, isPreview }) => {
  if (command !== 'serve' || isPreview) return {};
  const token = loadEnv(mode, backendDir, 'APP_API_TOKEN').APP_API_TOKEN;
  if (!token) throw new Error('Set APP_API_TOKEN in backend/.env before starting the frontend.');
  const target = new URL(
    loadEnv(mode, frontendDir, 'VITE_API_URL').VITE_API_URL || 'http://127.0.0.1:8000',
  );
  if (
    !['http:', 'https:'].includes(target.protocol) ||
    !['localhost', '127.0.0.1', '[::1]'].includes(target.hostname) ||
    target.username ||
    target.password ||
    target.pathname !== '/' ||
    target.search ||
    target.hash
  ) {
    throw new Error(
      'Local VITE_API_URL must be a localhost API origin, such as http://127.0.0.1:8000.',
    );
  }
  return {
    plugins: [
      {
        name: 'local-api-access',
        configureServer(server) {
          server.middlewares.use(localApiGuard);
        },
      },
    ],
    server: {
      host: '127.0.0.1',
      cors: false,
      proxy: {
        '/api': {
          target: target.origin,
          changeOrigin: true,
          headers: { Authorization: `Bearer ${token}` },
        },
      },
    },
  };
});
