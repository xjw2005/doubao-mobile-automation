# ADR-002: Doubao Mobile Share-Page Extractor

- **Status**: Accepted
- **Date**: 2026-06-24
- **Scope**: `mobile_auto_doubao/`, root `runner.py`, `doubao-source-extractor/`

## Context

The legacy Doubao mobile flow extracted thinking and sources by operating the
Android UI directly:

1. expand expert/thinking content in the app
2. expand reference panels
3. tap each source
4. copy each source URL from the mobile share sheet

That route is slow and viewport-dependent. The DeepSeek mobile flow uses a
better pattern: the phone only asks the question and creates a share link; the
desktop share page is then treated as the source of truth for answer, thinking,
and sources.

## Probe Result

Observed Doubao answer share URL:

```text
https://www.doubao.com/thread/xdcb2a74c43c188a398007be7292897bd
```

Accepted URL pattern:

```text
^https?://(www\.)?doubao\.com/thread/[A-Za-z0-9]+(?:[/?#].*)?$
```

The share page server-rendered HTML contains a `mergeLoaderData` script with:

```text
shareInfo.data.message_snapshot.message_list
```

The verified extraction route for the sample link returned:

- answer: 1166 chars
- thinkingContent: 1034 chars
- sources: 12 real external URLs

## Decision

Add `doubao-source-extractor/` and `mobile_auto_doubao/source_extractor_bridge.py`.

When `runner.py --extract-sources` is enabled:

1. Mobile Doubao still performs app launch, new chat, thinking toggle, question send, answer wait, and answer share-link capture.
2. Mobile-side source extraction is skipped.
3. Optional `--link-only` also skips mobile expert/thinking capture.
4. The captured `https://www.doubao.com/thread/<id>` URL is passed to the Node extractor.
5. The extractor parses `shareInfo.data.message_snapshot.message_list`.
6. Extracted share-page `answer`, `thinkingContent`, and `sources` override mobile-captured content.

Default behavior remains unchanged when `--extract-sources` is not enabled.

## Field Mapping

- Assistant message: latest `message_list[]` item with `user_type == 2`
- Thinking title: `content_block[].content.thinking_block.finish_title`
- Thinking text: `text_block.text` whose `parent_id` points to the thinking block
- Answer text: `text_block.text` with no thinking parent
- Sources: `search_query_result_block.results[].text_card`
- Source URL: `text_card.url`
- Source title: `text_card.title`
- Source platform: `text_card.sitename`, with URL-domain fallback

## Consequences

- The Doubao mobile runner can now match the DeepSeek architecture without
  changing the existing Feishu field schema.
- Old mobile source extraction stays available as a fallback by omitting
  `--extract-sources`.
- The extractor first tries CDP page content, then falls back to fetching the
  original share HTML because Doubao may remove SSR scripts after hydration.
- When the JS extractor is given a Feishu base token and writes the source
  table itself, Python answer writeback skips its legacy source-table write to
  avoid duplicate source rows.

## Validation

```powershell
node scripts/probe_doubao_share_content.js --url "https://www.doubao.com/thread/xdcb2a74c43c188a398007be7292897bd"
node doubao-source-extractor/run.js --url "https://www.doubao.com/thread/xdcb2a74c43c188a398007be7292897bd" --extract-only
python runner.py --task tasks/example.json --dry-run --extract-sources --link-only
```
