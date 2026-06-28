# Debug Script Index

Run these scripts from a restored `mobile-auto-doubao` workspace after copying `references/scripts/*.py` into `scripts/`.

## UI And URL Probes

`probe_current_ui_urls_adb.py`
: Read-only ADB probe. Dumps current UI XML and searches visible text/resource ids/content descriptions for `http(s)` URLs.

```powershell
python scripts\probe_current_ui_urls_adb.py --serial <serial>
```

`probe_current_ui_urls.py`
: Similar URL probe used during earlier local development.

`probe_ui_layout.py`
: Dumps and summarizes the current Doubao UI layout. Use when selectors such as input, send, share, source title, or new-chat controls stop matching.

```powershell
python scripts\probe_ui_layout.py --serial <serial>
```

## Source Link Probes

`probe_source_urls_by_click_adb.py`
: Clicks visible source entries and checks opened pages for exposed URLs. Useful for confirming that UI XML alone usually does not expose real source URLs.

```powershell
python scripts\probe_source_urls_by_click_adb.py --serial <serial> --limit 3 --wait-seconds 5
```

`probe_source_urls_by_share_copy_adb.py`
: Baseline share-copy-paste-read probe. Use when source extraction regresses.

```powershell
python scripts\probe_source_urls_by_share_copy_adb.py --serial <serial> --limit 3
```

`test_source_links_snake.py`
: Current richer source-link smoke test. Expands references, iterates visible sources, shares, copies, reads clipboard or paste text, and writes a report.

```powershell
python scripts\test_source_links_snake.py --serial <serial> --limit 3 --output-dir outputs\source-snake
python scripts\test_source_links_snake.py --serial <serial> --limit 0 --output-dir outputs\source-snake
```

## Expert Answer And Share Probes

`probe_reference_expand_adb.py`
: Tests reference-panel expansion, especially expert/deep-think pages where the first tap may expand thinking and the second tap opens sources.

`test_expert_mode_full.py`
: End-to-end expert/deep-think probe for answer and source behavior.

`test_expert_mode_full_answer.py`
: Focused expert answer capture probe.

`test_answer_share_link.py`
: Tests answer-level share link extraction.

## Practical Debug Order

1. Run `python runner.py --task <task> --dry-run`.
2. If device/app state is unclear, run `probe_ui_layout.py`.
3. If source URLs fail, run `test_source_links_snake.py --limit 3`.
4. If expert answer capture fails, run `test_expert_mode_full_answer.py`.
5. Patch constants/selectors in `mobile_auto_doubao/constants.py` or flow logic in `doubao_app.py`, `source_links.py`, `expert_answer.py`, or `answer_share.py`.
