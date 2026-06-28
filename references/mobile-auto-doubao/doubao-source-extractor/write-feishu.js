#!/usr/bin/env node
/**
 * Write Doubao sources to a Feishu Bitable.
 *
 * Fields written:
 *   来源标题, 来源URL, 引用来源类型, 引用来源平台, 关联自然问句
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

function parseArgs(argv) {
  const args = {
    sources: '',
    baseToken: '',
    tableId: '',
    naturalQuestion: '',
    aiPlatform: '豆包',
    dryRun: false,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--sources') args.sources = argv[++i];
    else if (arg === '--base-token') args.baseToken = argv[++i];
    else if (arg === '--table-id') args.tableId = argv[++i];
    else if (arg === '--natural-question') args.naturalQuestion = argv[++i];
    else if (arg === '--ai-platform') args.aiPlatform = argv[++i];
    else if (arg === '--dry-run') args.dryRun = true;
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
  console.log(`Usage: node write-feishu.js [options]

Write Doubao sources to a Feishu Bitable.

Options:
  --sources <file>         JSON file from extract-sources.js (required)
  --base-token <token>     Feishu Bitable app_token (required)
  --table-id <id>          Feishu Bitable table_id (required)
  --natural-question <id>  Natural question ID to associate (required)
  --ai-platform <name>     AI platform name. Default: 豆包
  --dry-run                Print what would be written without writing
  --help                   Show this help
`);
}

function inferSourceType(source) {
  const text = `${source.url || ''} ${source.title || ''} ${source.summary || ''} ${source.platform || ''}`;
  return /douyin\.com|iesdouyin\.com|抖音|视频|bilibili|youku|iqiyi/u.test(text) ? '视频' : '图文';
}

function normalizePlatform(value) {
  const text = String(value || '').replace(/\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b/g, '').trim();
  if (!text || /^\d+$/.test(text)) return '';
  return text.replace(/\s+\d{4}([/-]\d{1,2}){0,2}$/u, '').trim();
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, "''")}'`;
}

function createFeishuRecords(baseToken, tableId, fields, rows, dryRun = false) {
  if (!rows.length) return { skipped: true, reason: 'no-rows', tableId, count: 0 };
  const jsonPayload = JSON.stringify({ fields, rows });
  const tmpFile = path.join(process.cwd(), `doubao-feishu-${Date.now()}.json`);
  fs.writeFileSync(tmpFile, jsonPayload, 'utf8');
  const args = [
    'base', '+record-batch-create',
    '--base-token', baseToken,
    '--table-id', tableId,
    '--json', `@${path.basename(tmpFile)}`,
    '--format', 'json',
  ];
  if (dryRun) args.push('--dry-run');
  try {
    const result = execFileSync(
      'powershell',
      ['-ExecutionPolicy', 'Bypass', '-Command', `lark-cli ${args.map(shellQuote).join(' ')}`],
      { encoding: 'utf8', maxBuffer: 50 * 1024 * 1024 },
    );
    return JSON.parse(result);
  } finally {
    fs.unlinkSync(tmpFile);
  }
}

function buildRows(data, naturalQuestion) {
  const fields = ['来源标题', '来源URL', '引用来源类型', '引用来源平台', '关联自然问句'];
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const rows = sources.map(source => [
    source.title || source.url || '',
    source.url || '',
    inferSourceType(source),
    normalizePlatform(source.platform),
    naturalQuestion,
  ]);
  return { fields, rows };
}

function writeSources(baseToken, tableId, sourcesData, naturalQuestion, dryRun = false) {
  if (!sourcesData.ok || !Array.isArray(sourcesData.sources)) {
    throw new Error(`Invalid sources data: ok=${sourcesData.ok}, sources=${typeof sourcesData.sources}`);
  }
  const { fields, rows } = buildRows(sourcesData, naturalQuestion);
  return createFeishuRecords(baseToken, tableId, fields, rows, dryRun);
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.sources) throw new Error('--sources is required');
  if (!args.baseToken) throw new Error('--base-token is required');
  if (!args.tableId) throw new Error('--table-id is required');
  if (!args.naturalQuestion) throw new Error('--natural-question is required');

  const data = JSON.parse(fs.readFileSync(args.sources, 'utf8'));
  console.log(`Writing ${Array.isArray(data.sources) ? data.sources.length : 0} source rows to Feishu Bitable...`);
  console.log(`Base: ${args.baseToken}, Table: ${args.tableId}`);
  console.log(`Natural question: ${args.naturalQuestion}`);
  console.log('---');
  const { rows } = buildRows(data, args.naturalQuestion);
  rows.forEach((row, i) => {
    console.log(`[${i + 1}] ${row[0].slice(0, 40)} | ${row[1]} | ${row[2]} | ${row[3]} | ${row[4]}`);
  });
  console.log('---');

  if (args.dryRun) {
    console.log('[dry-run] Skipping actual write.');
    return;
  }

  const result = writeSources(args.baseToken, args.tableId, data, args.naturalQuestion, args.dryRun);
  console.log('Write result:', JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main().catch(error => {
    console.error(`[write-feishu] failed: ${error.stack || error.message}`);
    process.exit(1);
  });
}

module.exports = { writeSources, buildRows, inferSourceType, normalizePlatform };
