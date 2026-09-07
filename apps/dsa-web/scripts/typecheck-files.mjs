// Use TypeScript's compiler API to check only explicit roots, with real imports.
// Imported modules are resolved for types; diagnostics are limited to the roots.
import ts from 'typescript';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const files = process.argv.slice(2).map(file => resolve(root, file));
if (!files.length || files.some(file => !file.startsWith(resolve(root, 'src') + '/') || !/\.(ts|tsx)$/.test(file))) {
  throw new Error('Usage: node scripts/typecheck-files.mjs src/file.ts ...');
}
const config = ts.readConfigFile(resolve(root, 'tsconfig.app.json'), ts.sys.readFile);
if (config.error) throw new Error(ts.flattenDiagnosticMessageText(config.error.messageText, '\n'));
const parsed = ts.parseJsonConfigFileContent({ ...config.config, include: [], files }, ts.sys, root);
const program = ts.createProgram(files, { ...parsed.options, noEmit: true, incremental: false });
const diagnostics = [
  ...parsed.errors, ...program.getOptionsDiagnostics(),
  ...files.flatMap(file => {
    const source = program.getSourceFile(file);
    if (!source) throw new Error(`File not found: ${file}`);
    return [...program.getSyntacticDiagnostics(source), ...program.getSemanticDiagnostics(source)];
  }),
];
if (diagnostics.length) {
  console.error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: name => name, getCurrentDirectory: () => root, getNewLine: () => '\n',
  }));
  process.exitCode = 1;
} else console.log(`Scoped TypeScript check passed (${files.length} files; not a project-wide check).`);
