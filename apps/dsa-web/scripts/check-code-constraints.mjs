import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const root = new URL('..', import.meta.url).pathname;
const repoRoot = join(root, '..', '..');
const srcRoot = join(root, 'src');
const staticAssetsRoot = join(repoRoot, 'static', 'assets');

const MAX_FRONTEND_SOURCE_LINES = 600;
const MAX_ENTRY_CHUNK_KB = 150;
const MAX_PAGE_CHUNK_KB = 150;
const MAX_VENDOR_CHUNK_KB = 260;
// assistant-ui 是单一第三方生态(@assistant-ui/* + assistant-stream/cloud)，无法再拆，
// 单独放宽预算。留余量应对小版本升级。
const VENDOR_CHUNK_BUDGET_OVERRIDES = {
  'vendor-assistant-ui': 360,
};

function walk(dir, predicate, result = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      walk(path, predicate, result);
    } else if (predicate(path)) {
      result.push(path);
    }
  }
  return result;
}

function lineCount(path) {
  return readFileSync(path, 'utf8').split('\n').length;
}

function checkSourceLineBudgets() {
  const files = walk(srcRoot, (path) => (
    /\.(ts|tsx)$/.test(path)
    && !path.includes('/__tests__/')
    && !path.endsWith('.d.ts')
  ));

  const failures = files
    .map((path) => ({ path, lines: lineCount(path) }))
    .filter((item) => item.lines > MAX_FRONTEND_SOURCE_LINES);

  if (failures.length > 0) {
    console.error(`Frontend source files must stay under ${MAX_FRONTEND_SOURCE_LINES} lines:`);
    for (const item of failures) {
      console.error(`  ${relative(repoRoot, item.path)}: ${item.lines}`);
    }
    return false;
  }

  console.log(`Source line budget OK (${files.length} files, max ${MAX_FRONTEND_SOURCE_LINES} lines).`);
  return true;
}

function chunkBudgetFor(name) {
  if (name.startsWith('vendor-')) {
    // 去掉 hash 后缀(-xxxxxxxx.js)得到 chunk 基名用于匹配 override。
    const base = name.replace(/-[A-Za-z0-9_]{8,}\.js$/, '');
    if (base in VENDOR_CHUNK_BUDGET_OVERRIDES) return VENDOR_CHUNK_BUDGET_OVERRIDES[base];
    return MAX_VENDOR_CHUNK_KB;
  }
  if (name.startsWith('index-')) return MAX_ENTRY_CHUNK_KB;
  if (/Page-/.test(name)) return MAX_PAGE_CHUNK_KB;
  return null;
}

function checkBuildChunkBudgets() {
  try {
    statSync(staticAssetsRoot);
  } catch {
    console.log('Build assets not found; skipping chunk budget check. Run npm run build first.');
    return true;
  }

  const files = walk(staticAssetsRoot, (path) => path.endsWith('.js'));
  const failures = files
    .map((path) => {
      const name = path.split('/').pop() || path;
      return { path, name, kb: statSync(path).size / 1024, budget: chunkBudgetFor(name) };
    })
    .filter((item) => item.budget !== null && item.kb > item.budget);

  if (failures.length > 0) {
    console.error('Build chunk budgets exceeded:');
    for (const item of failures) {
      console.error(`  ${relative(repoRoot, item.path)}: ${item.kb.toFixed(1)} KiB > ${item.budget} KiB`);
    }
    return false;
  }

  console.log(`Build chunk budget OK (${files.length} JS assets checked).`);
  return true;
}

const ok = [checkSourceLineBudgets(), checkBuildChunkBudgets()].every(Boolean);
if (!ok) {
  process.exitCode = 1;
}
