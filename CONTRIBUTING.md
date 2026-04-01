# Contributing to coverage-robot

Thanks for your interest in contributing! This project generates Robot Framework
tests from Java coverage reports and is licensed under Apache 2.0.

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/coverage-robot.git
   ```
3. **Create a branch** for your change:
   ```bash
   git checkout -b my-feature
   ```
4. Make your changes, commit, push, and open a **Pull Request** against `main`.

## Code Style

- Python 3.6+ compatible -- no f-strings-only assumptions; maintain broad compatibility.
- Follow the patterns already established in the codebase.
- Keep functions focused and files reasonably sized.
- Add docstrings to public functions and classes.

## Testing

Run the test suite before submitting a PR:

```bash
python3 -m pytest
```

Validate your changes against the sample data in `coverage_parser/demo/`. If you
add a new feature, include corresponding test cases and sample data where
appropriate.

## Commit Messages

- Use the **imperative mood** ("Add feature", not "Added feature").
- Keep the subject line under 72 characters.
- Reference related issues when applicable (e.g., `Fix #42`).

Example:

```
Add JaCoCo CSV parser for branch coverage

Parse branch-level coverage from JaCoCo CSV exports and map results
to Robot Framework test case templates. Closes #15.
```

## Reporting Bugs

Open a [GitHub Issue](../../issues) with:

- A clear, descriptive title.
- Steps to reproduce the problem.
- Expected vs. actual behavior.
- Relevant logs, coverage report snippets, or error output.

## Code of Conduct

- Be respectful and constructive in all interactions.
- Welcome newcomers and help them get started.
- Focus feedback on the code, not the person.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE).
