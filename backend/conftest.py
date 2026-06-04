"""
Pytest configuration for backend tests.

Tests live in backend/tests/. Run from the backend directory:
    venv/bin/pytest                          # all unit tests
    venv/bin/pytest tests/test_parse.py -v   # AI parse tests (requires Ollama)
Or via dev.sh from the project root:
    ./dev.sh test
"""
