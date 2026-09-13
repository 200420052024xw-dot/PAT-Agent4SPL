# CSP# Inputs

Place CSP# models (`*.csp`) in this directory. Running
`python src/pipeline.py` verifies every CSP# file directly inside this folder.

The input model must contain at least one active `#assert` statement. Assertions
inside `// ...` or `/* ... */` comments are ignored.
