# qc-french-first

Python tool to scan and flag language compliance issues for Quebec's French-first requirements (Bill 96). Scans text content against configurable rules and generates reports.

## Stack

- Python 3
- YAML configuration
- HTML report templates
- Bash runner script

## Project structure

```
scanner/     — core scanning logic
templates/   — HTML report templates
tools/       — utility scripts
config.yml   — scanning rules and thresholds
main.py      — entry point
run.sh       — quick-start runner
```

## Quickstart

```bash
# Run the scanner
./run.sh

# Or directly
python main.py
```

## Configuration

Edit `config.yml` to customize scanning rules, thresholds, and output format.

## Why this project

Quebec's Bill 96 strengthens French language requirements for businesses. This tool automates the detection of potential compliance issues in text content — useful for organizations that need to verify their communications meet French-first standards.
