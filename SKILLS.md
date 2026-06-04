# Calamo Skills

## Skill: Academic Visual Integration
**Goal**: Insert a table or chart into a document with proper academic analysis and GOST-compliant labeling.

### Workflow (The 5-Step Block)
When a section instruction contains a visual requirement, follow these steps exactly:
1. **STEP 1 — LEAD-IN**: Last sentence of a paragraph must link to the visual (e.g., "The dynamics of indicators are reflected in Figure 1.").
2. **STEP 2 — TITLE**: New line with exact title in **Sentence case**: "Table N — [Title]" or "Figure N — [Title]".
3. **STEP 3 — MARKER**: New line with technical marker: `[ВСТАВИТЬ_ТАБЛИЦУ_N]` or `[ВСТАВИТЬ_ГРАФИК_N]`.
4. **STEP 4 — SOURCE**: New line with FULL bibliography reference: "Источник: подготовлено автором на основе источника: [Полная запись 1]." or "...на основе источников: [Запись 1]; [Запись 2].".
5. **STEP 5 — ANALYSIS**: Minimum 2-3 deep paragraphs (150-200 words) describing trends, causes, and impacts of the shown data.

### Rules
- NEVER use Markdown formatting.
- NEVER place two blocks consecutively.
- Use diverse lead-in phrases (don't repeat "As shown in table...").

## Skill: Source Generation and Mapping
**Goal**: Create a bibliography and map analytical visuals to real data sources.

### Workflow
1. Generate `SourceItem` list based on topic.
2. When generating `TableSpec` or `ChartSpec`, look at the `sources_content`.
3. Set `source_note` in the spec to a specific item from the bibliography (e.g., "Источник: [3]").
4. Ensure the table/chart data actually matches the content of that source.
