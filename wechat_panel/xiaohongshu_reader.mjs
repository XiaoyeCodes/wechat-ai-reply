// Read the default search results without the upstream adapter's optional
// filter-panel clicks (whose layout varies by account). Uses the existing
// Browser Bridge session; no login, cookie extraction, or account writes.
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
const [root, query, rawLimit] = process.argv.slice(2);
const limit = Number(rawLimit);
if (!root || !query || query.length > 500 || !Number.isInteger(limit) || limit < 1 || limit > 10) throw new Error('Invalid search');
const {Page} = await import(pathToFileURL(join(root, 'dist/src/browser/page.js')).href);
const page = new Page('relay-xhs-' + randomUUID(), 40, undefined, 'background', 'browser');
try {
  await page.goto('https://www.xiaohongshu.com/search_result?keyword=' + encodeURIComponent(query) + '&source=web_search_result_notes');
  let rows = [];
  for (let attempt = 0; attempt < 6; attempt++) {
    const result = await page.evaluate(`(() => {
      const body = document.body?.innerText || '';
      if (/请求太频繁|访问频次异常|安全限制|请完成验证/.test(body)) return {error:'platform_verification_required'};
      const cards = [...document.querySelectorAll('section.note-item, section:has(a[href*="/search_result/"]), section:has(a[href*="/explore/"])')];
      const rows = cards.map(card => {
        const links = [...card.querySelectorAll('a[href]')];
        const link = links.find(a => /\\/(explore|search_result)\\/[a-f0-9]{24}/i.test(a.href) && a.href.includes('xsec_token='));
        if (!link) return null;
        const url = new URL(link.href);
        if (url.protocol !== 'https:' || !['www.xiaohongshu.com','xiaohongshu.com'].includes(url.hostname)) return null;
        const title = card.querySelector('.title')?.innerText?.trim();
        if (!title) return null;
        return {title, author:card.querySelector('.author .name, .author-wrapper .name')?.innerText?.trim() || '', url:url.href};
      }).filter(Boolean);
      if (!rows.length && /登录后查看搜索结果/.test(body)) return {error:'login_required'};
      return {rows};
    })()`);
    // Page.evaluate normally returns the unwrapped object. Accept the old
    // Bridge envelope too, without treating a protocol response as content.
    const data = result?.rows || result?.error ? result : result?.result?.value || result?.value;
    if (data?.error) throw new Error(data.error);
    if (data?.rows?.length) {rows = data.rows.slice(0, limit); break;}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  if (!rows.length) throw new Error('empty_results_or_login_required');
  process.stdout.write(JSON.stringify(rows));
} catch (error) {
  process.stderr.write(String(error.message).slice(0, 300));
  process.exitCode = 1;
} finally {
  await page.closeWindow().catch(() => {});
}
