# Calamo Project Memory

## Architecture Decisions
- **Visual-First Generation (2026-04-29)**: Pipeline refactored to generate `ChartSpec` and `TableSpec` before calling `generate_section`. This ensures the LLM knows exactly which visuals to reference and can write analytical lead-ins.
- **Full GOST Citations in Sources (2026-04-29)**: `source_note` now contains full bibliographic entries instead of just numbers `[N]`. This follows the requirement: "подготовлено автором на основе источников: [Полная запись]".
- **Sentence Case Naming**: Explicitly enforced sentence case for table and figure titles to avoid ALL CAPS and improve readability.
- **Dynamic Visual Sources**: Added `source_note` to `TableSpec` and `ChartSpec`. LLM is instructed to map visuals to specific bibliography items `[N]` instead of generic "composed by author".
- **Regex Marker System**: Document assembly uses `[ВСТАВИТЬ_ТАБЛИЦУ_N]` and `[ВСТАВИТЬ_ГРАФИК_N]` markers. This allows precise placement by the LLM and clean replacement by `DocxBuilder`.
- **Skip Header Logic**: `DocxBuilder` methods now support `skip_header=True`. When True, the LLM is responsible for writing the visual's title and source in the body text (academic style), while the builder only inserts the table/image data.

## Fragile Areas
- **f-string Braces in LLM Prompts**: Prompts generating JSON must use double braces `{{ }}` to avoid Python `ValueError: Invalid format specifier`.
- **Bibliography Parsing**: The `_parse_json` method in `LLMService` is a bottleneck. Always ensure prompts explicitly ask for "VALID JSON ONLY" to avoid parsing failures.
- **Section Word Count**: `target_words` is an estimate. LLM tends to write less than requested. Analysis requirements (150+ words per visual) help boost volume and quality.

## Known Issues to Avoid
- **Stuck Visuals**: Don't assign more than 2 visuals to a single small section. If visuals "cluster" at the end of a section, ensure `fig_instr` explicitly forbids consecutive placement.
- **Missing Lead-ins**: LLM occasionally forgets to reference a table. The `figures_instruction` must be "MANDATORY" in the prompt.
- **Math Formatting (Cyrillic)**: Cyrillic text in equations MUST be wrapped in `\text{...}`. Backend uses `_fix_omml_cyrillic` to inject Cambria Math properties.

## Deployment & SSH
- **Server**: `root@185.5.75.211`
- **Project Root (Server)**: `/opt/calamo`
- **SSH Key**: `~/.ssh/antigravity_new_key` (created on 2026-05-06)
- **Deploy Command**: Run `./deploy.sh` from the local project root.
- **GitHub**: Uses HTTPS token (configured in `.git/config`).
