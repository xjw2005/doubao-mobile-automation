#!/usr/bin/env node
/**
 * Probe Doubao share-thread data.
 *
 * This script opens/fetches a real Doubao share link and summarizes the
 * server-rendered shareInfo payload. Use it when Doubao changes the page shape
 * or when validating a new URL format.
 */

const fs = require('fs');
const path = require('path');
const {
  extractShareId,
  extractDoubaoSourcesViaSsrHtml,
  extractShareInfoPayloadsFromHtml,
} = require('../doubao-source-extractor/extract-sources');

function parseArgs(argv) {
  const args = {
    url: '',
    outputDir: '',
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--url') args.url = argv[++i];
    else if (arg === '--output-dir') args.outputDir = argv[++i];
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
  console.log(`Usage: node scripts/probe_doubao_share_content.js --url <doubao-thread-url> [--output-dir outputs/doubao]

Fetch a Doubao share page, parse shareInfo.data.message_snapshot, and print the
answer/thinking/source summary used by doubao-source-extractor.
`);
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.url) throw new Error('--url is required');
  const shareId = extractShareId(args.url);
  if (!shareId) throw new Error('URL must look like https://www.doubao.com/thread/<id>');

  const response = await fetch(args.url, {
    headers: {
      'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
      'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    },
  });
  if (!response.ok) throw new Error(`fetch failed: ${response.status} ${response.statusText}`);
  const html = await response.text();
  const payloads = extractShareInfoPayloadsFromHtml(html);
  const extracted = extractDoubaoSourcesViaSsrHtml(html, shareId);
  const summary = {
    url: args.url,
    shareId,
    route: extracted.sourceFormat,
    ok: extracted.ok,
    reason: extracted.reason,
    payloadCount: payloads.length,
    title: extracted.title,
    messageCount: extracted.messageCount,
    answerLength: String(extracted.answer || '').length,
    thinkingLength: String(extracted.thinkingContent || '').length,
    sourceCount: extracted.count,
    sourceTitles: (extracted.sources || []).map(source => source.title),
    sourceUrls: (extracted.sources || []).map(source => source.url),
  };

  if (args.outputDir) {
    fs.mkdirSync(args.outputDir, { recursive: true });
    const safeId = shareId.replace(/[^A-Za-z0-9_-]/g, '_');
    fs.writeFileSync(path.join(args.outputDir, `${safeId}-summary.json`), JSON.stringify(summary, null, 2) + '\n', 'utf8');
    fs.writeFileSync(path.join(args.outputDir, `${safeId}-extracted.json`), JSON.stringify(extracted, null, 2) + '\n', 'utf8');
    fs.writeFileSync(path.join(args.outputDir, `${safeId}-payloads.json`), JSON.stringify(payloads, null, 2) + '\n', 'utf8');
  }

  console.log(JSON.stringify(summary, null, 2));
}

if (require.main === module) {
  main().catch(error => {
    console.error(`[probe_doubao_share_content] failed: ${error.stack || error.message}`);
    process.exit(1);
  });
}
