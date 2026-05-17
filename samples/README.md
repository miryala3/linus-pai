# Linus PAI — Sample Programs

All samples talk to the PAI server at `http://localhost:9480` (or `PAI_API` env var).
Start the server first: `bash runpai.sh`

| File | What it does | Key flags |
|---|---|---|
| `chat.py` | Multi-turn terminal chat with personas and file context | `--persona coder` `--web` `--file src/` |
| `code_review.py` | Full AI code review with severity ratings | `--diff` `--watch src/` |
| `test_gen.py` | Generate pytest test suites for any Python file | `--missing src/` `--out tests/` |
| `bug_fixer.py` | Error-driven bug fixer from tracebacks | `--loop app.py` `--apply` |
| `doc_writer.py` | Add docstrings, write READMEs and API references | `--readme` `--api-ref` |
| `refactor.py` | Targeted code refactoring with diff preview | `--goal "add type hints"` |
| `commit_writer.py` | Smart git commit message generator | `--commit` `--hook` |
| `data_analyst.py` | Natural-language CSV/JSON/SQLite analysis | `--query "top 10 ..."` `--report` |
| `sql_helper.py` | Natural language → SQL with live execution | `--dsn postgresql://...` |
| `web_scraper.py` | Describe what to scrape, get structured data | `--want "all prices"` |
| `agentic_task.py` | Autonomous multi-step task runner | `--interactive` `--pipeline tasks.json` |

## Quick demos

```bash
# Code review your staged git changes
python samples/code_review.py --diff

# Generate tests for a module
python samples/test_gen.py mymodule.py --out tests/

# Fix a crashing script automatically (run/fix/run loop)
python samples/bug_fixer.py --loop myapp.py

# Chat with the coding persona about a file
python samples/chat.py --persona coder --file src/main.py

# Analyse a CSV in natural language
python samples/data_analyst.py sales.csv --query "which product has the highest margin?"

# Natural-language SQL on a SQLite database
python samples/sql_helper.py orders.db

# Scrape structured data from any page
python samples/web_scraper.py --url https://news.ycombinator.com --want "top 10 titles and scores"

# Smart commit message
python samples/commit_writer.py --commit

# Autonomous agent task
python samples/agentic_task.py "Search for the latest MoE research papers and summarise the top 3"
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `PAI_API` | `http://localhost:9480` | PAI server URL |

All samples work fully offline except `web_scraper.py` and queries with `--web`.
No data is sent to any external service — inference runs on your own hardware.
