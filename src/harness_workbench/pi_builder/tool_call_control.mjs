// Adapter-owned native Pi extension. User code receives a frozen tool event,
// not Pi's provider/session/registration API. Trusted code, not an OS sandbox.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { registerHooks } from 'node:module';

export default async function (pi) {
  const config = JSON.parse(fs.readFileSync(process.env.HWB_PI_CONTROL_CONFIG, 'utf8'));
  const trace = process.env.HWB_PI_CONTROL_TRACE;
  const emit = (value) => fs.appendFileSync(trace, JSON.stringify(value) + '\n');
  const root = path.resolve(config.root);
  const files = new Set(Object.keys(config.files).map(x => path.resolve(root, x)));
  const inside = url => url?.startsWith('file:') && fileURLToPath(url).startsWith(root + path.sep);
  // Normal module resolution from the declared tree can reach only its bound
  // files or Node builtins. No package install/discovery is requested.
  registerHooks({resolve(specifier, context, nextResolve) {
    if (!inside(context.parentURL)) return nextResolve(specifier, context);
    if (specifier.startsWith('node:')) return nextResolve(specifier, context);
    if (!specifier.startsWith('./') && !specifier.startsWith('../')) throw new Error('undeclared module dependency');
    const result = nextResolve(specifier, context);
    if (!result.url.startsWith('file:') || !files.has(fileURLToPath(result.url))) throw new Error('undeclared module dependency');
    return result;
  }});
  const freeze = value => {
    if (value && typeof value === 'object') { for (const child of Object.values(value)) freeze(child); Object.freeze(value); }
    return value;
  };
  let handler;
  try {
    // Keep the import native rather than letting the harness's TS loader
    // rewrite the declared ES-module dependency boundary.
    handler = (await new Function('url', 'return import(url)')(pathToFileURL(config.entry).href)).default;
    if (typeof handler !== 'function') throw new Error('default export must be a tool-call function');
    emit({type:'loaded', entry:path.relative(root, config.entry)});
  } catch (error) { emit({type:'error', phase:'load', error:String(error)}); }
  pi.on('tool_call', async event => {
    const call = {call_id:event.toolCallId, tool:event.toolName, path:event.input?.path};
    let timer;
    try {
      if (!handler) throw new Error('control did not load');
      const result = await Promise.race([
        Promise.resolve(handler(freeze(structuredClone(event)), freeze(structuredClone(config.settings)))),
        new Promise((_, reject) => { timer=setTimeout(() => reject(new Error('control timeout')), config.timeout_ms); })
      ]);
      if (result !== undefined && (!result || typeof result !== 'object' || Array.isArray(result) ||
          Object.keys(result).some(k => !['block','reason'].includes(k)) || typeof result.block !== 'boolean' ||
          ('reason' in result && typeof result.reason !== 'string'))) throw new Error('invalid tool-call control result');
      emit({type:'decision', ...call, block:result?.block === true});
      return result;
    } catch (error) {
      emit({type:'error', phase:'tool_call', ...call, error:String(error)});
      return {block:true, reason:'Workbench control failed closed'};
    } finally { clearTimeout(timer); }
  });
}
