// Run the same PostgreSQL wire-protocol experiment using a local PGlite database.
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createRequire } from 'node:module';
import { isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const toolRoot = process.env.CASE_PGLITE_TOOL_ROOT;
const dataDir = process.env.CASE_PGLITE_DATA_DIR;
if (!toolRoot || !isAbsolute(toolRoot) || !dataDir) {
  throw new Error('Set CASE_PGLITE_TOOL_ROOT to an absolute tool directory and CASE_PGLITE_DATA_DIR to a dedicated database directory');
}
const require = createRequire(resolve(toolRoot, 'package.json'));
const { PGlite } = require('@electric-sql/pglite');
const { PGLiteSocketServer } = require('@electric-sql/pglite-socket');
const { vector } = require('@electric-sql/pglite-pgvector');
const database = await PGlite.create(resolve(dataDir), { extensions: { vector } });
const listener = new PGLiteSocketServer({ db: database, host: '127.0.0.1', port: 0 });
try {
  await listener.start();
  const address = new URL(`postgresql://${listener.getServerConn()}`);
  if (address.hostname !== '127.0.0.1' || !address.port) {
    throw new Error('PGlite failed to provide a loopback TCP listener');
  }
  const script = fileURLToPath(new URL('./experiment.py', import.meta.url));
  const args = process.argv.slice(2);
  const pythonArgs = args[0] === '--app' ? ['-B', '-X', 'utf8', '-m', 'legalweb']
    : ['-I', '-B', '-X', 'utf8', script, ...args];
  const child = spawn(process.env.CASE_LAB_PYTHON || 'python', pythonArgs, {
    stdio: 'inherit',
    env: { ...process.env, CASE_VECTOR_DSN: `postgresql://postgres@${address.host}/postgres?sslmode=disable` },
  });
  const [code] = await once(child, 'exit');
  process.exitCode = code ?? 1;
} finally {
  try {
    await listener.stop();
  } finally {
    await database.close();
  }
}
