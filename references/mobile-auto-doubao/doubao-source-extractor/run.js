#!/usr/bin/env node
/**
 * Doubao Source Extractor - Main Entry
 *
 * Complete pipeline: extract answer/thinking/sources from a Doubao share page
 * via CDP, then optionally write sources to a Feishu Bitable.
 */

const fs = require('fs');
const { extractSources } = require('./extract-sources');
const { writeSources, buildRows } = require('./write-feishu');

const DEFAULT_CDP_URL = process.env.CDP_URL || 'http://127.0.0.1:9222';

function parseArgs(argv) {
  const args = {
    cdp: DEFAULT_CDP_URL,
    url: '',
    naturalQuestion: '',
    baseToken: '',
    tableId: '',
    output: '',
    timeout: 15000,
    dryRun: false,
    extractOnly: false,
    writeOnly: false,
    sources: '',
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--cdp') args.cdp = argv[++i];
    else if (arg === '--url') args.url = argv[++i];
    else if (arg === '--natural-question') args.naturalQuestion = argv[++i];
    else if (arg === '--question-id') args.naturalQuestion = argv[++i];
    else if (arg === '--base-token') args.baseToken = argv[++i];
    else if (arg === '--table-id') args.tableId = argv[++i];
    else if (arg === '--output') args.output = argv[++i];
    else if (arg === '--timeout') args.timeout = Number(argv[++i]);
    else if (arg === '--dry-run') args.dryRun = true;
    else if (arg === '--extract-only') args.extractOnly = true;
    else if (arg === '--write-only') args.writeOnly = true;
    else if (arg === '--sources') args.sources = argv[++i];
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
  console.log(`Doubao Source Extractor - Complete Pipeline

Extract answer, thinking content, and reference sources from a Doubao share page.

Usage:
  node run.js --url "<doubao-share-url>" --question-id NQ-001 \\
    --base-token <token> --table-id <table-id>

Options:
  --url <url>               Doubao share URL (required for extract step)
  --natural-question <id>   Question ID to associate (required for write step)
  --question-id <id>        Alias for --natural-question
  --base-token <token>      Feishu Bitable app_token (required for write step)
  --table-id <id>           Feishu Bitable table_id (required for write step)
  --cdp <url>               CDP endpoint. Default: ${DEFAULT_CDP_URL}
  --timeout <ms>            Page/API wait timeout. Default: 15000
  --output <file>           Save extracted JSON to a file
  --sources <file>          Skip extraction, write from existing JSON file
  --extract-only            Only extract, don't write to Feishu
  --write-only              Only write to Feishu from --sources file
  --dry-run                 Don't actually write to Feishu
  --help                    Show this help
`);
}

async function main() {
  const args = parseArgs(process.argv);

  if (args.writeOnly) {
    if (!args.sources) throw new Error('--sources is required for --write-only mode');
    if (!args.baseToken) throw new Error('--base-token is required');
    if (!args.tableId) throw new Error('--table-id is required');
    if (!args.naturalQuestion) throw new Error('--natural-question/--question-id is required');
  } else if (args.extractOnly) {
    if (!args.url) throw new Error('--url is required for --extract-only mode');
  } else {
    if (!args.url) throw new Error('--url is required');
    if (!args.naturalQuestion) throw new Error('--natural-question/--question-id is required');
    if (!args.baseToken) throw new Error('--base-token is required');
    if (!args.tableId) throw new Error('--table-id is required');
  }

  let sourcesData;
  if (args.writeOnly) {
    console.log(`[1/2] Loading sources from ${args.sources}...`);
    sourcesData = JSON.parse(fs.readFileSync(args.sources, 'utf8'));
  } else {
    console.log('[1/2] Extracting content from Doubao share page...');
    console.log(`      URL: ${args.url}`);
    console.log(`      CDP: ${args.cdp}`);
    sourcesData = await extractSources(args.cdp, args.url, args.timeout);

    if (!sourcesData.ok) {
      console.error(`Extraction failed: ${sourcesData.reason || 'unknown'}`);
      process.exit(1);
    }
    console.log(`      Route: ${sourcesData.sourceFormat || 'unknown'}`);
    console.log(`      Answer chars: ${String(sourcesData.answer || '').length}`);
    console.log(`      Thinking chars: ${String(sourcesData.thinkingContent || '').length}`);
    if (Array.isArray(sourcesData.searchQueries) && sourcesData.searchQueries.length) {
      console.log(`      Search keywords: ${sourcesData.searchQueries.join('、')}`);
    }
    console.log(`      Extracted ${sourcesData.count || 0} sources`);

    if (args.output) {
      fs.writeFileSync(args.output, JSON.stringify(sourcesData, null, 2) + '\n', 'utf8');
      console.log(`      Saved to ${args.output}`);
    }
  }

  if (args.extractOnly) {
    console.log('\nExtraction result:');
    console.log(JSON.stringify(sourcesData, null, 2));
    return;
  }

  console.log(`\n[2/2] Writing ${Array.isArray(sourcesData.sources) ? sourcesData.sources.length : 0} sources to Feishu Bitable...`);
  console.log(`      Base: ${args.baseToken}`);
  console.log(`      Table: ${args.tableId}`);
  console.log(`      Question ID: ${args.naturalQuestion}`);
  console.log('      ---');
  const { rows } = buildRows(sourcesData, args.naturalQuestion);
  rows.forEach((row, i) => {
    console.log(`      [${i + 1}] ${row[0].slice(0, 40)} | ${row[1]} | ${row[2]} | ${row[3]} | ${row[4]}`);
  });
  console.log('      ---');

  if (args.dryRun) {
    console.log('\n[dry-run] Skipping actual write to Feishu.');
    return;
  }

  const result = writeSources(args.baseToken, args.tableId, sourcesData, args.naturalQuestion, args.dryRun);
  console.log('\nWrite result:', JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main().catch(error => {
    console.error(`[run] failed: ${error.stack || error.message}`);
    process.exit(1);
  });
}
