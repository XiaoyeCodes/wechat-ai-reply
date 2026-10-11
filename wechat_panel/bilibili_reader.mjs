// Compatibility path for OpenCLI bridges that support browser sessions but
// reject adapter pre-navigation. Reuse its read-only subtitle adapter.
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
const [root,bvid]=process.argv.slice(2);
if(!root||!/^BV[a-zA-Z0-9]{10}$/.test(bvid||''))throw new Error('Invalid video ID');
const moduleURL=p=>pathToFileURL(join(root,p)).href;
const {Page}=await import(moduleURL('dist/src/browser/page.js'));
const {getRegistry}=await import(moduleURL('dist/src/registry.js'));
await import(moduleURL('clis/bilibili/subtitle.js'));
const page=new Page('relay-subtitle-'+randomUUID(),40,undefined,'background','browser');
try{
  await page.goto('https://www.bilibili.com');
  const result=await getRegistry().get('bilibili/subtitle').func(page,{bvid});
  process.stdout.write(JSON.stringify(result));
}catch{
  process.stderr.write('Bilibili subtitle unavailable. Check browser connection and login.');
  process.exitCode=1;
}finally{
  await page.closeWindow().catch(()=>{});
}
