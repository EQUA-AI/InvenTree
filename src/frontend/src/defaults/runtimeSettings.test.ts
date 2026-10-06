import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

/** Exercise the actual entry-point settings without booting React or the API. */
function startupSettings(dev: boolean, supplied: Record<string, unknown> = {}) {
  const source = readFileSync(new URL('../main.tsx', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile(
    'main.tsx',
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TSX
  );
  const declarations = new Set([
    'IS_DEV',
    'IS_DEMO',
    'IS_DEV_OR_DEMO',
    'loaded_vals'
  ]);
  const settings = parsed.statements.filter((statement) => {
    if (ts.isVariableStatement(statement)) {
      return statement.declarationList.declarations.some(
        (declaration) =>
          ts.isIdentifier(declaration.name) &&
          declarations.has(declaration.name.text)
      );
    }
    return (
      ts.isExpressionStatement(statement) &&
      /^(Object\.keys\(loaded_vals\)|window\.INVENTREE_SETTINGS\s*=)/.test(
        statement.getText(parsed)
      )
    );
  });
  expect(settings.length).toBeGreaterThanOrEqual(4);
  const code = settings
    .map((statement) => statement.getText(parsed))
    .join('\n')
    .replaceAll('import.meta.env', 'runtimeEnv');
  const window = {
    location: { origin: 'https://approved.example.test' },
    INVENTREE_SETTINGS: supplied
  };
  runInNewContext(
    ts.transpileModule(code, {
      compilerOptions: { module: ts.ModuleKind.CommonJS }
    }).outputText,
    { window, runtimeEnv: { DEV: dev, VITE_DEMO: 'true' }, exports: {} },
    { timeout: 1000 }
  );
  return window.INVENTREE_SETTINGS as {
    server_list: Record<string, { host: string }>;
    default_server: string;
    show_server_selector: boolean;
  };
}

describe('entry-point server settings', () => {
  it('ignores a retired build flag and defaults to the current production host', () => {
    const settings = startupSettings(false);
    expect(Object.keys(settings.server_list)).toEqual(['server-current']);
    expect(settings.default_server).toBe('server-current');
    expect(settings.show_server_selector).toBe(false);
  });

  it('retains local development without offering an external demo host', () => {
    const settings = startupSettings(true);
    expect(Object.keys(settings.server_list)).toEqual([
      'server-localhost',
      'server-current'
    ]);
    expect(settings.default_server).toBe('server-localhost');
    expect(settings.show_server_selector).toBe(true);
  });

  it('preserves explicitly supplied server configuration', () => {
    const supplied = {
      server_list: { authorized: { host: 'https://client.example.test/' } },
      default_server: 'authorized',
      show_server_selector: true
    };
    expect(startupSettings(false, supplied)).toMatchObject(supplied);
  });
});
