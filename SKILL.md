---
name: doubao-mobile-automation
description: Current Doubao mobile automation workflow for running task JSON or Feishu Base rows through the Android Doubao app with Python plus ADB, including expert answers, answer share links, real source URLs, optional Feishu writeback, parallel-device output overrides, and current debug options.
---

# Doubao Mobile Automation

Use this skill to run, debug, or migrate the current `mobile-auto-doubao` runner on another machine.
It covers task JSON runs, Feishu Base collection/writeback, JSON-configured table IDs, ADB device selection, ADB Keyboard input, answer share-page extraction, and source-link extraction.

## Core Workflow

1. Read `references/README.md` before setup, ADB/device changes, or Feishu Base changes.
2. Restore `references/mobile-auto-doubao/` into a workspace if the project is not already present.
3. Run `python runner.py --task <task.json> --dry-run` before any live run.
4. Use a unique `--output` for each parallel process.
5. Use live runs only when an Android device is online and the Doubao app is already logged in.
6. After each run, report the output path, status counts, answer text, `thinkingContent`, `answerShareUrl`, source titles, source URLs, and any blocked/partial/failed reasons.

## Runner Commands

Task JSON mode:

```powershell
python runner.py --task tasks/example.json --dry-run
python runner.py --task tasks/example.json
python runner.py --task tasks/example.json --serial emulator-5556 --output results/example-emulator-5556.json --debug
```

Override ADB/device from the command line:

```powershell
python runner.py --task tasks/<task>.json --adb "<adb-path>" --serial <serial>
```

For parallel runs, pass a unique `--serial` and a unique `--output` per process.
The runner refuses to guess when multiple adb devices are online, which prevents cross-device runs.

Feishu Base mode:

```powershell
python runner.py --base-url "<feishu-base-url>" --base-start 1 --base-end 10 --dry-run
python runner.py --base-url "<feishu-base-url>" --base-start 1 --base-end 10 --writeback --mark-collected --collect-account 18870501682
```

Feishu Base mode with a JSON table-ID config, recommended when the input question table and the two writeback tables vary but field names stay the same:

```powershell
python runner.py --feishu-config configs/feishu-doubao-example.json --base-start 1 --base-end 10 --dry-run
python runner.py --feishu-config configs/feishu-doubao-example.json --base-start 1 --base-end 10 --writeback --mark-collected --extract-sources --link-only --cdp-url http://127.0.0.1:9222
```

Minimal config shape:

```json
{
  "input": {
    "baseUrl": "https://example.feishu.cn/base/<baseToken>?table=<questionTableId>&view=<viewId>"
  },
  "writeback": {
    "answerTableId": "<answerTableId>",
    "sourceTableId": "<sourceTableId>"
  },
  "collectAccount": "18870501682"
}
```

The input table uses `问题文本`, `问题ID`, and `是否开启深度思考`; `是否本次采集` is optional and defaults to `是` when absent. The runner also tolerates legacy input fields `问题` and `关联自然问句` during migration.
The answer and source writeback tables use `问题ID` for the question identifier field; only the table IDs are swapped through the JSON file.

For Doubao share-page extraction, use `--extract-sources --link-only`.
In this mode the runner captures the mobile answer share link, parses the Doubao share page through `doubao-source-extractor/`, fills answer/thinking/sources from the share-page snapshot, and lets the JS extractor write source rows.
If the share link or share-page answer cannot be verified, the runner skips Feishu answer writeback and does not mark the source row collected.

`--collect-account` controls the `采集账号` field written into the Feishu answer table.
Set it per operator/device when you run multiple devices in parallel.

If you need to keep screenshots, `currentFocus`, or XML artifacts, add `--debug`.

## Required References

- `references/README.md`: migration, environment setup, ADB checks, ADB Keyboard setup, task JSON contract, Feishu Base mode, output contract, troubleshooting.
- `references/scripts/README.md`: probe and debug script index.
- `references/mobile-auto-doubao/`: runnable Doubao project snapshot containing `runner.py` (entry point), `mobile_auto_doubao/` (the package), `doubao-source-extractor/` (the share-page JS extractor), `configs/feishu-doubao-example.json`, and `requirements.txt`.
- `references/scripts/`: current probe and debug scripts copied from the project.
- `references/tasks/`: task JSON examples.
- `references/tools/keyboardservice-debug.apk`: ADB Keyboard APK used for reliable Chinese text input.

## Operating Rules

- Preserve question text exactly.
- Prefer one fresh Doubao chat per question unless a task explicitly requests reuse.
- Do not automate login or captcha. Report those cases as `blocked`.
- Do not fabricate source URLs from source titles. Real source URLs come from share/copy/paste or clipboard extraction.
- Treat `partial` as useful output: answer text may exist even when some source links or share links failed.
- Keep generated `results/`, `outputs/`, screenshots, XML dumps, and logs outside the skill unless the user explicitly asks to archive evidence.

## ADB Keyboard Notes

Use `com.android.adbkeyboard/.AdbIME` for Chinese input.

The normal path is:

1. Install `references/tools/keyboardservice-debug.apk` on the target device.
2. Open the Android input-method settings page and enable `ADB Keyboard`.
3. Switch the current input method to `com.android.adbkeyboard/.AdbIME`.
4. Confirm it with `adb shell ime list -s` and `adb shell dumpsys input_method`.

If live runs fail with `adb_keyboard_not_installed`, install the APK and set the IME again.

On some emulator ROMs, `ime enable` and `ime set` are blocked by policy.
In that case, use the Settings UI plus `dumpsys input_method` to confirm the service is active.
If the built-in keyboard keeps taking over, temporarily disable that built-in IME while running the script.

Some emulator ROMs also block `adb shell input swipe` with `INJECT_EVENTS` security errors.
The runner falls back to `PAGE_UP` / `PAGE_DOWN` key events for scrolling answer and source panels, so those ROMs remain usable without extra setup.

## Fast Debug Path

1. Check devices with `adb devices`.
2. Confirm the device lists `com.android.adbkeyboard/.AdbIME`.
3. Validate the task with `python runner.py --task tasks/example.json --dry-run`.
4. If live source extraction fails, use `references/scripts/test_source_links_snake.py` or `references/scripts/probe_source_urls_by_share_copy_adb.py`.
5. If UI selectors fail, use `references/scripts/probe_ui_layout.py` and compare resource ids in `mobile_auto_doubao/constants.py`.
6. For parallel runs, give each process a distinct `--serial` and `--output`.
