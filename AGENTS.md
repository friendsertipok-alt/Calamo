# Calamo Agent Rules

## Project Overview
Calamo is an automated platform for generating academic papers (courseworks, essays, etc.) compliant with GOST standards. It uses a Python (FastAPI) backend and an LLM-driven generation pipeline.

## Project Structure
- `backend/app/pipeline/generator.py`: Main orchestration logic (Step-by-step generation).
- `backend/app/services/llm_service.py`: Prompt engineering and LLM interaction.
- `backend/app/services/docx_builder.py`: Word document generation (python-docx).
- `backend/app/schemas/order.py`: Data models and visual specifications.
- `backend/app/services/chart_generator.py`: Matplotlib-based chart generation.

## Critical Working Rules
1. **GOST Compliance**: All documents MUST follow strict GOST 7.0.100-2018 (bibliography) and general academic formatting.
2. **No Markdown**: The LLM must NEVER output Markdown (`#`, `**`, etc.) in the body text.
3. **Visual Block Synchronization**: Always generate visual specs (TableSpec/ChartSpec) BEFORE writing section text.
4. **Visual Naming**: Titles must be in **Sentence case** (starts with capital, then lower case), NOT ALL CAPS. No typos.
5. **Full GOST Sources**: Visual source notes must contain full GOST citations, prefixed with "подготовлено автором на основе источника:" (singular) or "источников:" (plural). No numeric [N] links here.
6. **Analysis Requirement**: Every visual must be followed by at least 150-200 words of analysis.
7. **No Consecutive Visuals**: Never place two tables/charts back-to-back without intervening text.

## Technical Commands
- **Run Backend**: `cd backend && source venv/bin/activate && uvicorn app.main:app --reload`
- **Deploy to Prod**: `./deploy.sh` (requires SSH config — see deploy.sh)
- **Venv Path**: `backend/venv`

## Final Report Requirements
After each task, report:
1. What was changed (files and logic).
2. What checks were performed (tests, deployment).
3. Risks or follow-up items.
