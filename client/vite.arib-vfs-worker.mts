import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { buildSync } from 'esbuild';

/** 通常の TypeScript をコンパイルし、SDK Worker は未改変のまま読み込む。 */
export function loadARIBVFSWorkerSource(): string {
    const middleware = compileWorker('./src/workers/ARIBVFSProxy.ts');
    const sdk = readFileSync(fileURLToPath(new URL(
        './node_modules/libaribhtml5/dist/sdk/arib-vfs-sw.js', import.meta.url,
    )), 'utf-8');
    return `${middleware}\n${sdk}`;
}

/** 文書の先頭で実行するコードも、文字列への埋め込みをせず通常の JS ファイルとして配信する。 */
export function loadARIBRuntimeBootstrapSource(): string {
    return compileWorker('./src/workers/ARIBRuntimeBootstrap.ts');
}

function compileWorker(entry: string): string {
    const result = buildSync({
        entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
        bundle: true,
        write: false,
        format: 'iife',
        platform: 'browser',
        target: 'es2022',
    });
    return result.outputFiles[0].text;
}
