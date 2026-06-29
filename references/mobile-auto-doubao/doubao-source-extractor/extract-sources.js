#!/usr/bin/env node
/**
 * Doubao Source Extractor
 *
 * Connects to an already-running Chrome instance via CDP, opens a Doubao share
 * thread (https://www.doubao.com/thread/<id>), and extracts the authoritative
 * answer, thinking text, and reference sources from the server-rendered
 * `shareInfo.data.message_snapshot.message_list` payload.
 *
 * The mobile app still only needs to create/copy the answer share link. This
 * script then uses the share page as the source of truth, matching the
 * DeepSeek mobile flow while using Doubao's own share-page data shape.
 */

const fs = require('fs');

function loadChromium() {
  const candidates = [
    'playwright-core',
    '../deepseek-source-extractor/node_modules/playwright-core',
    '../qianwen-source-extractor/node_modules/playwright-core',
  ];
  const errors = [];
  for (const candidate of candidates) {
    try {
      return require(candidate).chromium;
    } catch (error) {
      errors.push(`${candidate}: ${error.message}`);
    }
  }
  throw new Error(`Could not load playwright-core. Run "npm install" in doubao-source-extractor. Tried: ${errors.join(' | ')}`);
}

const DEFAULT_CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';

function parseArgs(argv) {
  const args = {
    cdp: DEFAULT_CDP_URL,
    output: '',
    url: '',
    timeout: 15000,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--cdp') args.cdp = argv[++i];
    else if (arg === '--output') args.output = argv[++i];
    else if (arg === '--url') args.url = argv[++i];
    else if (arg === '--timeout') args.timeout = Number(argv[++i]);
    else if (arg === '--help' || arg === '-h') {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return args;
}

function printHelp() {
  console.log(`Usage: node extract-sources.js [options]

Extract answer, thinking content, and reference sources from a Doubao share page.

Options:
  --cdp <url>       CDP endpoint. Default: ${DEFAULT_CDP_URL}
  --url <url>       Doubao share thread URL (required)
  --timeout <ms>    Page readiness timeout. Default: 15000
  --output <file>   Save JSON output to a file
  --help            Show this help

Example:
  node extract-sources.js --url "https://www.doubao.com/thread/xdcb..." --output sources.json
`);
}

function extractShareId(url) {
  const match = String(url || '').match(/\/thread\/([A-Za-z0-9]+)/);
  return match ? match[1] : '';
}

function decodeHtmlEntities(text) {
  return String(text || '')
    .replace(/&quot;/g, '"')
    .replace(/&#34;/g, '"')
    .replace(/&#x22;/gi, '"')
    .replace(/&#x27;/gi, "'")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function cleanText(value) {
  return String(value || '')
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function attrValue(tag, name) {
  const re = new RegExp(`${name}="([\\s\\S]*?)"`);
  const match = tag.match(re);
  return match ? decodeHtmlEntities(match[1]) : '';
}

function parseJsonString(value, fallback = null) {
  if (typeof value !== 'string' || !value.trim()) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function extractShareInfoPayloadsFromHtml(html) {
  const payloads = [];
  const seen = new Set();
  const scriptRe = /<script\b[^>]*data-fn-name="mergeLoaderData"[^>]*>/g;
  let match;
  while ((match = scriptRe.exec(html))) {
    const tag = match[0];
    const argsText = attrValue(tag, 'data-fn-args');
    const args = parseJsonString(argsText, null);
    if (!Array.isArray(args)) continue;
    const loaderItems = Array.isArray(args[1]) ? args[1] : [];
    for (const item of loaderItems) {
      const routerArgs = Array.isArray(item.routerDataFnArgs) ? item.routerDataFnArgs : [];
      for (const routerArg of routerArgs) {
        const payload = parseJsonString(routerArg, null);
        const shareInfo = payload && payload.data && payload.data.share_info;
        const snapshot = payload && payload.data && payload.data.message_snapshot;
        if (!shareInfo && !snapshot) continue;
        const key = shareInfo && shareInfo.share_id ? shareInfo.share_id : JSON.stringify(payload).slice(0, 200);
        if (seen.has(key)) continue;
        seen.add(key);
        payloads.push(payload);
      }
    }
  }
  return payloads;
}

function messageBlocks(message) {
  if (Array.isArray(message.content_block)) return message.content_block;
  const parsed = parseJsonString(message.content, []);
  return Array.isArray(parsed) ? parsed : [];
}

function messageRole(message) {
  if (message.user_type === 1 || String(message.role || '').toUpperCase() === 'USER') return 'user';
  if (message.user_type === 2 || String(message.role || '').toUpperCase() === 'ASSISTANT') return 'assistant';
  return '';
}

function extractTextBlock(block) {
  return cleanText(block && block.content && block.content.text_block && block.content.text_block.text);
}

function extractThinkingTitle(block) {
  const thinking = block && block.content && block.content.thinking_block;
  if (!thinking) return '';
  return cleanText(thinking.finish_title || thinking.unfold_streaming_title || thinking.streaming_title || '');
}

function platformFromUrl(url) {
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    const map = [
      ['bjnews', '新京报'], ['qianlong', '千龙网'], ['cnpiw', '中国报业网'],
      ['sina', '新浪'], ['sohu', '搜狐'], ['163.com', '网易'], ['ifeng', '凤凰网'],
      ['thepaper', '澎湃新闻'], ['caixin', '财新'], ['people.com.cn', '人民网'],
      ['xinhuanet', '新华网'], ['news.cn', '新华网'], ['chinanews', '中国新闻网'],
      ['china.com.cn', '中国网'], ['huanqiu', '环球网'], ['cctv', '央视网'],
      ['toutiao', '今日头条'], ['36kr', '36氪'], ['baidu', '百度'], ['weibo', '微博'],
      ['zhihu', '知乎'], ['douban', '豆瓣'], ['xiaohongshu', '小红书'],
      ['bilibili', '哔哩哔哩'], ['douyin', '抖音'], ['iqiyi', '爱奇艺'], ['youku', '优酷'],
      ['taobao', '淘宝'], ['tmall', '天猫'], ['jd.com', '京东'], ['csdn', 'CSDN'],
      ['juejin', '掘金'], ['wikipedia', '维基百科'], ['qq.com', '腾讯网'],
      ['weixin', '微信'], ['smzdm', '什么值得买'],
    ].sort((a, b) => b[0].length - a[0].length);
    const found = map.find(([key]) => host.includes(key));
    if (found) return found[1];
    const tlds = new Set(['com', 'cn', 'net', 'org', 'gov', 'edu', 'info', 'biz', 'xyz', 'top', 'io', 'cc']);
    const parts = host.split('.').filter(part => part && !tlds.has(part.toLowerCase()));
    return parts[parts.length - 1] || host;
  } catch {
    return '';
  }
}

function cardFromSearchResult(result) {
  if (!result || typeof result !== 'object') return null;
  const card =
    result.text_card ||
    result.video_card ||
    result.image_card ||
    result.web_card ||
    result;
  if (!card || typeof card !== 'object') return null;
  return card;
}

function sourceFromCard(card, indexHint) {
  const url = cleanText(
    card.url ||
    card.normalized_url ||
    card.normalizedUrl ||
    card.raw_url ||
    card.rawUrl ||
    card.original_doc_url ||
    card.originalDocUrl ||
    card.link ||
    '',
  );
  if (!/^https?:\/\//i.test(url)) return null;
  const title = cleanText(card.title || card.name || card.snippet_title || card.page_title || '') || url;
  const platform = cleanText(
    card.sitename ||
    card.site_name ||
    card.siteName ||
    card.platform ||
    card.source_name ||
    card.sourceName ||
    card.name ||
    '',
  ) || platformFromUrl(url);
  return {
    index: indexHint,
    title,
    url,
    normalizedUrl: cleanText(card.normalized_url || card.normalizedUrl || url),
    rawUrl: cleanText(card.raw_url || card.rawUrl || url),
    platform,
    summary: cleanText(card.summary || card.snippet || card.description || card.excerpt || '').slice(0, 2000),
    publishTime: cleanText(card.publish_time_second || card.publish_time || card.publishTime || card.date || card.published_at || ''),
    siteIcon: cleanText(card.logo_url || card.logoUrl || card.site_icon || card.siteIcon || card.icon || ''),
    type: cleanText(card.type || card.source_type || card.sourceType || ''),
    searchId: cleanText(card.search_id || card.searchId || ''),
    docId: cleanText(card.doc_id || card.docId || ''),
  };
}

function extractSourcesFromSearchBlock(block) {
  const search = block && block.content && block.content.search_query_result_block;
  const results = search && Array.isArray(search.results) ? search.results : [];
  const sources = [];
  for (const result of results) {
    const card = cardFromSearchResult(result);
    const source = sourceFromCard(card, sources.length + 1);
    if (source) sources.push(source);
  }
  return sources;
}

function dedupeSources(rawSources) {
  const seen = new Set();
  const sources = [];
  for (const source of rawSources) {
    if (!source || !source.url || seen.has(source.url)) continue;
    seen.add(source.url);
    sources.push({ ...source, index: sources.length + 1 });
  }
  return sources;
}

function chooseAssistantMessage(messages) {
  const assistants = messages.filter(message => messageRole(message) === 'assistant');
  if (assistants.length) return assistants[assistants.length - 1];
  return messages[messages.length - 1] || null;
}

function extractFromMessage(message) {
  const blocks = messageBlocks(message);
  const thinkingBlockIds = new Set();
  const thinkingTitleParts = [];
  const rawSources = [];
  let searchEnabled = false;
  const searchSummaries = [];
  const searchQueries = [];

  for (const block of blocks) {
    const thinkingTitle = extractThinkingTitle(block);
    if (thinkingTitle) {
      thinkingTitleParts.push(thinkingTitle);
      if (block.block_id) thinkingBlockIds.add(String(block.block_id));
    }
    const search = block && block.content && block.content.search_query_result_block;
    if (search) {
      searchEnabled = true;
      if (search.summary) searchSummaries.push(cleanText(search.summary));
      // 豆包联网搜索的关键词：search_query_result_block.queries
      const queries = Array.isArray(search.queries) ? search.queries : [];
      for (const query of queries) {
        const cleaned = cleanText(query);
        if (cleaned) searchQueries.push(cleaned);
      }
      rawSources.push(...extractSourcesFromSearchBlock(block));
    }
  }

  const textBlocks = blocks
    .map((block, idx) => ({ idx, block, text: extractTextBlock(block) }))
    .filter(item => item.text);

  const answerParts = [];
  const thinkingParts = [];
  for (const item of textBlocks) {
    const parentId = cleanText(item.block.parent_id || '');
    if (parentId && thinkingBlockIds.has(parentId)) thinkingParts.push(item.text);
    else answerParts.push(item.text);
  }

  let answer = cleanText(answerParts.join('\n\n'));
  let thinkingContent = cleanText(message.thinking_content || '');
  if (!thinkingContent) thinkingContent = cleanText(thinkingParts.join('\n\n'));
  if (!thinkingContent && thinkingBlockIds.size && textBlocks.length > 1 && answerParts.length === textBlocks.length) {
    thinkingContent = cleanText(textBlocks.slice(0, -1).map(item => item.text).join('\n\n'));
    answer = cleanText(textBlocks[textBlocks.length - 1].text);
  }
  if (!answer && textBlocks.length) {
    answer = cleanText(textBlocks[textBlocks.length - 1].text);
  }

  const thinkingTitle = cleanText(thinkingTitleParts.join('\n'));
  if (thinkingTitle && thinkingContent && !thinkingContent.includes(thinkingTitle)) {
    thinkingContent = `${thinkingTitle}\n\n${thinkingContent}`;
  } else if (thinkingTitle && !thinkingContent) {
    thinkingContent = thinkingTitle;
  }

  // 豆包特例：非深度思考模式下分享页没有 thinking_block / thinking_content，
  // 但联网搜索的关键词（search_query_result_block.queries）正是用户需要的"思考内容"。
  // 当没有真实思考内容时，用搜索关键词兜底，格式对齐飞书写回的「搜索关键词：A、B」约定。
  const uniqueSearchQueries = [...new Set(searchQueries.filter(Boolean))];
  if (!thinkingContent && uniqueSearchQueries.length) {
    thinkingContent = `搜索关键词：${uniqueSearchQueries.join('、')}`;
  }

  return {
    answer,
    thinkingContent,
    sources: dedupeSources(rawSources),
    searchEnabled,
    searchSummaries: [...new Set(searchSummaries.filter(Boolean))],
    searchQueries: uniqueSearchQueries,
    blockCount: blocks.length,
    textBlockCount: textBlocks.length,
  };
}

function extractDoubaoSourcesViaSsrHtml(html, shareId = '') {
  const payloads = extractShareInfoPayloadsFromHtml(html);
  const payload = payloads.find(item => {
    const sid = item && item.data && item.data.share_info && item.data.share_info.share_id;
    return !shareId || sid === shareId;
  }) || payloads[0];

  if (!payload || !payload.data) {
    return {
      ok: false,
      reason: 'share-info-payload-not-found',
      sourceFormat: 'doubao_share_ssr_message_snapshot',
      shareId,
      count: 0,
      sources: [],
    };
  }

  const messages = payload.data.message_snapshot && Array.isArray(payload.data.message_snapshot.message_list)
    ? payload.data.message_snapshot.message_list
    : [];
  if (!messages.length) {
    return {
      ok: false,
      reason: 'message-list-not-found',
      sourceFormat: 'doubao_share_ssr_message_snapshot',
      shareId,
      count: 0,
      sources: [],
    };
  }

  const assistant = chooseAssistantMessage(messages);
  const extracted = assistant ? extractFromMessage(assistant) : { answer: '', thinkingContent: '', sources: [], searchEnabled: false };
  const sources = extracted.sources || [];
  const resolvedShareId = shareId || cleanText(payload.data.share_info && payload.data.share_info.share_id);

  return {
    ok: Boolean(extracted.answer || extracted.thinkingContent || sources.length),
    reason: extracted.answer || extracted.thinkingContent || sources.length ? '' : 'answer-thinking-sources-not-found',
    url: '',
    title: cleanText(payload.data.share_info && payload.data.share_info.share_name),
    apiPath: 'data-fn-args.thread_(token)/page.shareInfo.data.message_snapshot.message_list',
    sourceFormat: 'doubao_share_ssr_message_snapshot',
    shareId: resolvedShareId,
    messageCount: messages.length,
    assistantMessageId: cleanText(assistant && assistant.message_id),
    answer: extracted.answer,
    thinkingContent: extracted.thinkingContent,
    searchEnabled: Boolean(extracted.searchEnabled || sources.length),
    searchSummaries: extracted.searchSummaries || [],
    searchQueries: extracted.searchQueries || [],
    count: sources.length,
    sources,
    debug: {
      payloadCount: payloads.length,
      blockCount: extracted.blockCount || 0,
      textBlockCount: extracted.textBlockCount || 0,
    },
  };
}

async function extractDoubaoSourcesViaDom(page, shareId) {
  return page.evaluate((sid) => {
    function clean(text) {
      return String(text || '').replace(/\s+/g, ' ').trim();
    }
    function platformFrom(url) {
      try {
        return new URL(url).hostname.replace(/^www\./, '');
      } catch {
        return '';
      }
    }
    const anchors = Array.from(document.querySelectorAll('a[href^="http"]'));
    const seen = new Set();
    const sources = [];
    for (const anchor of anchors) {
      let href = '';
      try {
        href = new URL(anchor.href, location.href).href;
      } catch {
        continue;
      }
      let host = '';
      try {
        host = new URL(href).hostname;
      } catch {
        continue;
      }
      if (/doubao\.com$/i.test(host) || /byteimg\.com$/i.test(host) || /bytednsdoc\.com$/i.test(host)) continue;
      if (seen.has(href)) continue;
      seen.add(href);
      sources.push({
        index: sources.length + 1,
        title: clean(anchor.getAttribute('title') || anchor.textContent || anchor.getAttribute('aria-label') || href),
        url: href,
        normalizedUrl: href,
        rawUrl: href,
        platform: platformFrom(href),
        summary: '',
        publishTime: '',
        type: '',
      });
    }
    return {
      ok: sources.length > 0 || clean(document.body && document.body.innerText).length > 0,
      reason: sources.length ? '' : 'no-external-links-in-dom',
      url: location.href,
      title: document.title,
      sourceFormat: 'doubao_share_dom_anchors',
      shareId: sid,
      answer: '',
      thinkingContent: '',
      searchEnabled: sources.length > 0,
      count: sources.length,
      sources,
    };
  }, shareId);
}

function pickDoubaoPage(contexts, shareUrl) {
  const pages = contexts.flatMap(context => context.pages());
  const shareId = extractShareId(shareUrl);
  if (shareId) {
    const byId = pages.find(page => page.url().includes(shareId));
    if (byId) return byId;
  }
  const sharePage = pages.find(page => /doubao\.com\/thread\//.test(page.url()));
  if (sharePage) return sharePage;
  return pages.find(page => /doubao\.com/.test(page.url())) || pages[0];
}

async function waitForDoubaoReady(page, timeout = 15000) {
  await page.waitForLoadState('domcontentloaded', { timeout }).catch(() => {});
  await page.waitForTimeout(500).catch(() => {});
}

async function extractViaFetch(shareUrl, shareId) {
  const response = await fetch(shareUrl, {
    headers: {
      'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
      'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    },
  });
  if (!response.ok) throw new Error(`fetch failed: ${response.status} ${response.statusText}`);
  const html = await response.text();
  const result = extractDoubaoSourcesViaSsrHtml(html, shareId);
  result.url = shareUrl;
  if (result.sourceFormat) result.sourceFormat = `${result.sourceFormat}_fetch`;
  return result;
}

async function extractSources(cdpUrl, shareUrl, timeout = 15000) {
  const shareId = extractShareId(shareUrl);
  if (!shareId) throw new Error('Could not extract share_id from --url argument.');

  let browser = null;
  try {
    const chromium = loadChromium();
    browser = await chromium.connectOverCDP(cdpUrl);
    let page = pickDoubaoPage(browser.contexts(), shareUrl);
    const onSharePage = page && page.url().includes(shareId);
    if (!onSharePage) {
      const context = browser.contexts()[0] || await browser.newContext();
      page = await context.newPage();
      await page.goto(shareUrl, { waitUntil: 'domcontentloaded', timeout });
    }

    await page.bringToFront();
    await waitForDoubaoReady(page, timeout);
    const html = await page.content();
    let result = extractDoubaoSourcesViaSsrHtml(html, shareId);
    result.url = page.url();
    if (!result.answer && !result.thinkingContent && !result.sources.length) {
      const fetchResult = await extractViaFetch(shareUrl, shareId).catch(error => ({
        ok: false,
        reason: `fetch-fallback-failed:${error.message}`,
        sources: [],
      }));
      if (fetchResult.answer || fetchResult.thinkingContent || (fetchResult.sources || []).length) {
        result = { ...fetchResult, debug: { ...(fetchResult.debug || {}), cdpSsrResult: result } };
      }
    }
    if (!result.answer && !result.thinkingContent && !result.sources.length) {
      const domResult = await extractDoubaoSourcesViaDom(page, shareId);
      result = domResult.ok ? { ...domResult, debug: { cdpSsrResult: result } } : { ...result, domFallback: domResult };
    }
    await page.close().catch(() => {});
    return result;
  } catch (error) {
    const result = await extractViaFetch(shareUrl, shareId);
    result.debug = { ...(result.debug || {}), cdpFallbackError: error.message };
    return result;
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.url) throw new Error('--url is required (Doubao share thread URL)');
  const result = await extractSources(args.cdp, args.url, args.timeout);
  const json = JSON.stringify(result, null, 2);
  if (args.output) fs.writeFileSync(args.output, `${json}\n`, 'utf8');
  console.log(json);
}

if (require.main === module) {
  main().catch(error => {
    console.error(`[extract-sources] failed: ${error.stack || error.message}`);
    process.exit(1);
  });
}

module.exports = {
  extractSources,
  extractDoubaoSourcesViaSsrHtml,
  extractDoubaoSourcesViaDom,
  extractShareInfoPayloadsFromHtml,
  pickDoubaoPage,
  waitForDoubaoReady,
  extractShareId,
};
